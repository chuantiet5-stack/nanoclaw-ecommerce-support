"""退款风控规则引擎（纯规则决策，不依赖 LLM）。

职责：给定订单、退货原因与顾客画像，输出退款审核决策：
- auto_approve：小额 + 签收 7 天内 + 无理由原因 + 账号正常 → 自动同意退款
- auto_reject：超 7 天无理由期且无质量问题 → 自动拒绝
- manual：大额 / 质量问题 / 高风险账号 / 频次超限 → 转人工

设计原则：
1. 决策一律由确定性规则给出，LLM 只负责收集信息（订单号、原因、证据），
   避免被顾客话术诱导放款；
2. 每次决策（无论结果）都追加审计日志，可回溯；
3. 自动通过的退款记录落盘，用于频次限制与退款率统计。

落地替换点：
- get_user_profile() 的风险分可替换为真实风控系统评分；
- 各阈值建议挪到 config.json，由商家按类目调整；
- 数据来源由本地 JSON 替换为平台售后 API（见 ApproveRefundTool）。
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from agent.tools.ecom.store import RETURN_WINDOW_DAYS, days_since, load_orders

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# 数据后端开关（生产落地 P0-1.1：与 store.py 一致，默认 json 不变）
DATA_BACKEND = os.getenv("DATA_BACKEND", "json").lower()

# ---------- 可调阈值（模拟阶段为常量，落地时挪入 config.json）----------
AUTO_APPROVE_ENABLED = True      # 总开关；上线初期建议 False（全部转人工，观察准确率后再放开）
AUTO_APPROVE_MAX_AMOUNT = 50     # 自动通过的单笔金额上限（元）
FREQ_WINDOW_DAYS = 30            # 频次统计窗口（天）
FREQ_MAX_TIMES = 2               # 窗口内自动通过次数上限（防薅羊毛）
MAX_REFUND_RATE = 0.3            # 账号历史退款率上限
MAX_RISK_SCORE = 70              # 风险分超过此值转人工

# 无理由类退货原因（命中任一关键词才可能自动通过）
NO_REASON_TAGS = ("不喜欢", "尺码不合适", "拍错", "买错", "不想要")
# 质量问题类原因（一律转人工，需照片/视频证据）
QUALITY_TAGS = ("质量问题", "破损", "开胶", "掉色", "故障", "假货", "与描述不符")


@dataclass
class RefundDecision:
    """退款决策结果。

    Attributes:
        action: 决策动作，auto_approve / auto_reject / manual
        reason: 决策依据说明（面向顾客与客服）
        rule: 命中的规则编号（审计用）
        profile: 参与决策的顾客画像快照（审计用）
    """

    action: str
    reason: str
    rule: str
    profile: dict | None = None


def _load_json(filename: str, default):
    path = DATA_DIR / filename
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(filename: str, data) -> None:
    path = DATA_DIR / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


# ---------- 退款记录与审计日志 ----------

def load_refund_records() -> list:
    """历史退款记录（自动 + 人工通过），用于退款率与频次统计。"""
    if DATA_BACKEND == "mysql":
        from db import repository
        from db.sync_bridge import run_async
        return run_async(repository.list_refund_records())
    return _load_json("refund_records.json", [])


def save_refund_record(record: dict) -> None:
    """追加一条退款记录。"""
    if DATA_BACKEND == "mysql":
        from db import repository
        from db.sync_bridge import run_async
        run_async(repository.append_refund_record(record))
        return
    records = load_refund_records()
    records.append(record)
    _save_json("refund_records.json", records)


def append_audit(entry: dict) -> None:
    """追加一条审计日志（每次审核决策都会记录，可回溯）。"""
    if DATA_BACKEND == "mysql":
        from db import repository
        from db.sync_bridge import run_async
        run_async(repository.append_audit_record(entry))
        return
    log = _load_json("refund_audit_log.json", [])
    log.append(entry)
    _save_json("refund_audit_log.json", log)


# ---------- 顾客画像 ----------

def get_user_profile(customer_id: str) -> dict:
    """统计顾客风控画像。

    - 历史订单数 / 退款次数 / 退款率（退款次数 ÷ 订单数）
    - 近 FREQ_WINDOW_DAYS 天自动通过次数（防刷）
    - 风险分（模拟：由退款率推导；落地后接真实风控系统评分）
    """
    orders = [o for o in load_orders() if o.get("customer_id") == customer_id]
    records = [r for r in load_refund_records() if r.get("customer_id") == customer_id]
    refund_rate = round(len(records) / len(orders), 2) if orders else 0.0

    cutoff = (datetime.now() - timedelta(days=FREQ_WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    recent_auto = sum(
        1 for r in records
        if r.get("decided_by") == "auto" and r.get("created_at", "") >= cutoff
    )

    # 模拟风险分：退款率驱动
    if refund_rate >= 0.5:
        risk_score = 80
    elif refund_rate > MAX_REFUND_RATE:
        risk_score = 60
    else:
        risk_score = 20

    return {
        "customer_id": customer_id,
        "order_count": len(orders),
        "refund_count": len(records),
        "refund_rate": refund_rate,
        "recent_auto_approve": recent_auto,
        "risk_score": risk_score,
    }


# ---------- 核心决策 ----------

def classify_refund(order: dict, reason: str, customer_id: str) -> RefundDecision:
    """退款审核决策（纯函数，可单测）。

    规则按顺序短路命中，编号用于审计追溯：
    R0 总开关 / R1 账号风险 / R2 频次超限 / R3 质量问题 /
    R4 超期 / R5 金额超限 / R6 原因白名单 / R7 退款率 / R8 全部通过

    Args:
        order: 订单 dict（需含 total、signed_at）
        reason: 退货原因（顾客原话）
        customer_id: 顾客 ID

    Returns:
        RefundDecision: 决策结果
    """
    reason = (reason or "").strip()
    profile = get_user_profile(customer_id)
    amount = order.get("total", 0)
    days = days_since(order.get("signed_at", ""))
    in_window = days <= RETURN_WINDOW_DAYS
    is_quality = any(tag in reason for tag in QUALITY_TAGS)

    def decide(action: str, why: str, rule: str) -> RefundDecision:
        return RefundDecision(action, why, rule, profile)

    if not AUTO_APPROVE_ENABLED:
        return decide("manual", "自动退款总开关未开启，全部转人工", "R0")
    if profile["risk_score"] > MAX_RISK_SCORE:
        return decide(
            "manual",
            f"账号风险分 {profile['risk_score']}（历史退款率 {profile['refund_rate']}）",
            "R1",
        )
    if profile["recent_auto_approve"] >= FREQ_MAX_TIMES:
        return decide(
            "manual",
            f"近 {FREQ_WINDOW_DAYS} 天自动通过已达 {profile['recent_auto_approve']} 次",
            "R2",
        )
    if is_quality:
        return decide("manual", "涉及质量问题，需人工核验照片/视频证据", "R3")
    if not in_window:
        return decide(
            "auto_reject",
            f"签收已 {days} 天，超过 {RETURN_WINDOW_DAYS} 天无理由退货期",
            "R4",
        )
    if amount > AUTO_APPROVE_MAX_AMOUNT:
        return decide(
            "manual",
            f"金额 ¥{amount} 超过自动通过上限 ¥{AUTO_APPROVE_MAX_AMOUNT}",
            "R5",
        )
    if not any(tag in reason for tag in NO_REASON_TAGS):
        return decide(
            "manual",
            f"退货原因（{reason or '未填写'}）不在无理由白名单内，需人工判断",
            "R6",
        )
    if profile["refund_rate"] > MAX_REFUND_RATE:
        return decide(
            "manual",
            f"历史退款率 {profile['refund_rate']} 超过上限 {MAX_REFUND_RATE}",
            "R7",
        )
    return decide(
        "auto_approve",
        f"小额 ¥{amount} + 签收 {days} 天内 + 无理由退货 + 账号正常",
        "R8",
    )


def classify_pre_receipt_refund(order: dict, customer_id: str) -> RefundDecision:
    """签收前（已付款未发货 / 已发货在途）的仅退款决策。

    顾客还没收到货，退款不涉及退货物流，风险远低于签收后退货，
    因此仅保留四道风控，不要求 7 天窗口与原因白名单：
    - 总开关关闭 / 账号高风险 / 频次超限 → 转人工（P0/P1/P2）
    - 金额 ≤ AUTO_APPROVE_MAX_AMOUNT → 自动通过（P8）；
      已发货订单由调用方先拦截物流再退款
    - 金额超限 → 转人工（P5）

    规则编号以 P 开头（Pre-receipt），与 classify_refund 的 R 系列区分，审计可追溯。
    """
    profile = get_user_profile(customer_id)
    amount = order.get("total", 0)

    def decide(action: str, why: str, rule: str) -> RefundDecision:
        return RefundDecision(action, why, rule, profile)

    if not AUTO_APPROVE_ENABLED:
        return decide("manual", "自动退款总开关未开启，全部转人工", "P0")
    if profile["risk_score"] > MAX_RISK_SCORE:
        return decide(
            "manual",
            f"账号风险分 {profile['risk_score']}（历史退款率 {profile['refund_rate']}）",
            "P1",
        )
    if profile["recent_auto_approve"] >= FREQ_MAX_TIMES:
        return decide(
            "manual",
            f"近 {FREQ_WINDOW_DAYS} 天自动通过已达 {profile['recent_auto_approve']} 次",
            "P2",
        )
    if amount > AUTO_APPROVE_MAX_AMOUNT:
        return decide(
            "manual",
            f"金额 ¥{amount} 超过自动退款上限 ¥{AUTO_APPROVE_MAX_AMOUNT}",
            "P5",
        )
    return decide(
        "auto_approve",
        f"小额 ¥{amount} 未签收仅退款 + 账号正常",
        "P8",
    )
