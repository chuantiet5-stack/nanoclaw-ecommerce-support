"""退款风控规则引擎测试（生产落地 P2-3.1）—— 涉及资金，必须全覆盖。

classify_refund 为纯函数（读 data/ 真实数据做画像），不产生写入。
当前 data/refund_records.json 不存在 → C001/C002 画像干净（0 退款记录）。
"""

from datetime import datetime, timedelta

import pytest

import agent.tools.ecom.refund_policy as rp
from agent.tools.ecom.refund_policy import classify_refund


def _signed(days_ago: int) -> str:
    """生成 N 天前的签收时间字符串。"""
    return (datetime.now() - timedelta(days=days_ago)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


@pytest.fixture
def normal_order():
    # 小额 + 签收 2 天内（7 天窗口内）
    return {"order_id": "SO001", "total": 30, "signed_at": _signed(2)}


@pytest.fixture
def expensive_order():
    return {"order_id": "SO002", "total": 200, "signed_at": _signed(2)}


class TestRefundPolicy:
    def test_r8_auto_approve_normal(self, normal_order):
        """R8: 小额 + 7 天内 + 无理由 + 正常账号 → 自动通过。"""
        decision = classify_refund(normal_order, "不喜欢", "C001")
        assert decision.action == "auto_approve"
        assert decision.rule == "R8"

    def test_r4_auto_reject_overdue(self):
        """R4: 超过 7 天无理由期 → 自动拒绝。"""
        order = {"order_id": "SO003", "total": 30, "signed_at": "2026-01-01 10:00:00"}
        decision = classify_refund(order, "不喜欢", "C001")
        assert decision.action == "auto_reject"
        assert decision.rule == "R4"

    def test_r5_amount_exceed(self, expensive_order):
        """R5: 金额超过自动通过上限（50 元）→ 转人工。"""
        decision = classify_refund(expensive_order, "不喜欢", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R5"

    def test_r3_quality_issue(self, normal_order):
        """R3: 质量问题 → 转人工（需证据）。"""
        decision = classify_refund(normal_order, "质量问题，开胶了", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R3"

    def test_r6_reason_not_in_whitelist(self, normal_order):
        """R6: 退货原因不在无理由白名单 → 转人工。"""
        decision = classify_refund(normal_order, "我就是想退钱", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R6"

    def test_r0_switch_off(self, normal_order, monkeypatch):
        """R0: 总开关关闭 → 全部转人工。"""
        monkeypatch.setattr(rp, "AUTO_APPROVE_ENABLED", False)
        decision = classify_refund(normal_order, "不喜欢", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R0"

    def test_r1_high_risk_account(self, normal_order, monkeypatch):
        """R1: 账号风险分超阈值 → 转人工。"""
        monkeypatch.setattr(rp, "get_user_profile", lambda cid: {
            "customer_id": cid, "order_count": 10, "refund_count": 8,
            "refund_rate": 0.8, "recent_auto_approve": 0, "risk_score": 80,
        })
        decision = classify_refund(normal_order, "不喜欢", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R1"

    def test_r2_freq_exceeded(self, normal_order, monkeypatch):
        """R2: 窗口内自动通过次数达上限 → 转人工。"""
        monkeypatch.setattr(rp, "get_user_profile", lambda cid: {
            "customer_id": cid, "order_count": 10, "refund_count": 1,
            "refund_rate": 0.1, "recent_auto_approve": 2, "risk_score": 20,
        })
        decision = classify_refund(normal_order, "不喜欢", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R2"

    def test_decision_contains_profile_snapshot(self, normal_order):
        """决策结果附带画像快照（审计用）。"""
        decision = classify_refund(normal_order, "不喜欢", "C001")
        assert decision.profile is not None
        assert decision.profile["customer_id"] == "C001"


class TestStoreHelpers:
    """store.py 公共校验/脱敏函数测试（业务验证清单项）。"""

    def test_mask_phone(self):
        from agent.tools.ecom.store import mask_phone
        assert mask_phone("13812345678") == "138****5678"

    def test_mask_address(self):
        from agent.tools.ecom.store import mask_address
        masked = mask_address("北京市朝阳区望京街道888号院3号楼2单元501")
        assert "****" in masked
        assert "501" not in masked[:11]

    def test_check_login(self):
        from agent.tools.ecom.store import check_login
        assert check_login(False) is not None   # 未登录返回提示
        assert check_login(True) is None        # 已登录放行

    def test_check_order_owner(self):
        from agent.tools.ecom.store import check_order_owner
        order = {"customer_id": "C001"}
        assert check_order_owner(order, "C001") is None      # 本人
        assert check_order_owner(order, "C002") is not None  # 越权

    def test_days_since_invalid(self):
        from agent.tools.ecom.store import days_since
        assert days_since("不合法时间") == 999  # 解析失败兜底
