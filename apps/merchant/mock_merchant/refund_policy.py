"""退款风控规则引擎（与 V4 agent/tools/ecom/refund_policy.py 的 R0-R8 规则严格对齐）。

决策输出三种动作：
- auto_approve：小额 + 签收 7 天内 + 无理由原因 + 账号正常 → 自动同意退款
- auto_reject：超 7 天无理由期且无质量问题 → 自动拒绝
- manual：大额 / 质量问题 / 高风险账号 / 频次超限 → 转人工

规则编号与 V4 保持一致，便于两侧审计日志对账：
R0 总开关 / R1 账号风险 / R2 频次超限 / R3 质量问题 /
R4 超期 / R5 金额超限 / R6 原因白名单 / R7 退款率 / R8 全部通过
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import config
from mock_merchant import db, rules


@dataclass
class RefundDecision:
    """退款决策结果。"""

    action: str          # auto_approve / auto_reject / manual
    reason: str          # 决策依据说明（面向顾客与客服）
    rule: str            # 命中的规则编号（审计用）
    profile: dict | None = None  # 参与决策的顾客画像快照（审计用）


def get_user_profile(customer_id: str) -> dict:
    """统计顾客风控画像（订单数 / 退款次数 / 退款率 / 近期自动通过次数 / 风险分）。

    风险分模拟逻辑与 V4 一致：退款率驱动。
    """
    orders = db.query(
        "SELECT order_id FROM orders WHERE customer_id = ?", (customer_id,)
    )
    # 与 V4 口径一致：只统计已通过（自动 + 人工）的退款记录
    records = db.query(
        "SELECT * FROM refunds WHERE customer_id = ? AND status IN ('审核通过','已退款')",
        (customer_id,),
    )
    order_count = len(orders)
    refund_count = len(records)
    refund_rate = round(refund_count / order_count, 2) if order_count else 0.0

    cutoff = (datetime.now() - timedelta(days=config.FREQ_WINDOW_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    recent_auto = sum(
        1 for r in records
        if r.get("review_type") == "auto" and (r.get("created_at") or "") >= cutoff
    )

    if refund_rate >= 0.5:
        risk_score = 80
    elif refund_rate > config.MAX_REFUND_RATE:
        risk_score = 60
    else:
        risk_score = 20

    return {
        "customer_id": customer_id,
        "order_count": order_count,
        "refund_count": refund_count,
        "refund_rate": refund_rate,
        "recent_auto_approve": recent_auto,
        "risk_score": risk_score,
    }


def classify_refund(order: dict, reason: str, customer_id: str) -> RefundDecision:
    """退款审核决策（纯函数，规则按顺序短路命中）。

    Args:
        order: 订单 dict（需含 total、signed_at）
        reason: 退货原因（顾客原话）
        customer_id: 顾客 ID
    """
    reason = (reason or "").strip()
    profile = get_user_profile(customer_id)
    amount = order.get("total", 0)
    days = rules.days_since(order.get("signed_at") or "")
    # 未签收的订单（未收到货）不适用签收时限
    signed = bool(order.get("signed_at"))
    in_window = (not signed) or days <= config.RETURN_WINDOW_DAYS
    is_quality = any(tag in reason for tag in config.QUALITY_TAGS)

    def decide(action: str, why: str, rule: str) -> RefundDecision:
        return RefundDecision(action, why, rule, profile)

    if not config.AUTO_APPROVE_ENABLED:
        return decide("manual", "自动退款总开关未开启，全部转人工", "R0")
    if profile["risk_score"] > config.MAX_RISK_SCORE:
        return decide(
            "manual",
            f"账号风险分 {profile['risk_score']}（历史退款率 {profile['refund_rate']}）",
            "R1",
        )
    if profile["recent_auto_approve"] >= config.FREQ_MAX_TIMES:
        return decide(
            "manual",
            f"近 {config.FREQ_WINDOW_DAYS} 天自动通过已达 {profile['recent_auto_approve']} 次",
            "R2",
        )
    if is_quality:
        return decide("manual", "涉及质量问题，需人工核验照片/视频证据", "R3")
    if not in_window:
        return decide(
            "auto_reject",
            f"签收已 {days} 天，超过 {config.RETURN_WINDOW_DAYS} 天无理由退货期",
            "R4",
        )
    if amount > config.AUTO_APPROVE_MAX_AMOUNT:
        return decide(
            "manual",
            f"金额 ¥{amount} 超过自动通过上限 ¥{config.AUTO_APPROVE_MAX_AMOUNT}",
            "R5",
        )
    if not any(tag in reason for tag in config.NO_REASON_TAGS):
        return decide(
            "manual",
            f"退货原因（{reason or '未填写'}）不在无理由白名单内，需人工判断",
            "R6",
        )
    if profile["refund_rate"] > config.MAX_REFUND_RATE:
        return decide(
            "manual",
            f"历史退款率 {profile['refund_rate']} 超过上限 {config.MAX_REFUND_RATE}",
            "R7",
        )
    return decide(
        "auto_approve",
        f"小额 ¥{amount} + 签收 {days} 天内 + 无理由退货 + 账号正常",
        "R8",
    )
