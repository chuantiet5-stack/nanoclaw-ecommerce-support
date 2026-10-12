"""签收前自动退款 + 下单付款 + 转人工提醒 + 鲜亮主题测试。

覆盖本轮需求：
1. 上架 5 个低于 50 元的商品（goods.json + SJ seed 双侧静态断言）
2. place_order：下单即付款，订单状态「已付款」
3. approve_refund 签收前分支：
   - 已付款未发货小额 → 机器人自动审核后直接退款
   - 已发货在途小额 → 自动拦截物流并退款
   - 大额（>50 元）→ 转人工（文案含「已提交人工审核」）
4. gateway 转人工提醒后台（静态源码断言检测标记存在）
5. index.html 鲜亮色系背景（静态断言）
"""

import shutil
from pathlib import Path

import pytest

import agent.tools.ecom.refund_policy as rp
import agent.tools.ecom.store as store
from agent.tools.ecom.approve_refund import ApproveRefundTool
from agent.tools.ecom.place_order import PlaceOrderTool
from agent.tools.ecom.refund_policy import classify_pre_receipt_refund

V4_ROOT = Path(__file__).resolve().parents[1]

# 本轮上架的 5 个低价商品 SKU（均低于 50 元）
LOW_PRICE_SKUS = ["CAP-001", "BOTTLE-001", "TOWEL-001", "ROPE-001", "BAND-001"]


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """把 data/ 种子数据拷到 tmp 目录并切换后端，测试写操作不污染真实数据。"""
    src = V4_ROOT / "data"
    for name in ("users.json", "goods.json", "orders.json",
                 "logistics.json", "tickets.json"):
        f = src / name
        if f.exists():
            shutil.copy(f, tmp_path / name)
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DATA_BACKEND", "json")
    monkeypatch.setattr(rp, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rp, "DATA_BACKEND", "json")
    return tmp_path


def _order(oid):
    for o in store.load_orders():
        if o["order_id"] == oid:
            return o
    return None


# ---------- 低价商品上架 ----------

class TestLowPriceGoods:
    def test_five_low_price_goods_in_v4(self):
        """v4 goods.json 含 5 个低于 50 元的新商品。"""
        goods = {g["sku"]: g for g in store.load_goods()}
        for sku in LOW_PRICE_SKUS:
            assert sku in goods, f"{sku} 未上架"
            assert goods[sku]["price"] < 50, f"{sku} 价格应低于 50 元"

    def test_low_price_goods_in_sj_seed(self):
        """SJ 商家服务 seed.py 同样包含这 5 个商品（双侧数据一致）。"""
        seed = (V4_ROOT.parent / "sj" / "mock_merchant" / "seed.py").read_text(
            encoding="utf-8"
        )
        for sku in LOW_PRICE_SKUS:
            assert f'"{sku}"' in seed, f"{sku} 未写入 SJ seed.py"


# ---------- place_order 下单付款 ----------

class TestPlaceOrder:
    async def test_place_order_paid(self, tmp_data):
        """下单即付款：订单落盘，状态「已付款」，金额正确。"""
        tool = PlaceOrderTool(actor="han", logged_in=True)
        n_before = len(store.load_orders())
        reply = await tool.execute(sku="TOWEL-001", qty=1)
        assert "下单成功" in reply and "已付款" in reply
        orders = store.load_orders()
        assert len(orders) == n_before + 1
        new = orders[-1]
        assert new["status"] == "已付款"
        assert new["customer_id"] == "C001"
        assert new["total"] == 19

    async def test_place_order_not_logged_in(self, tmp_data):
        """游客下单 → 统一登录提示。"""
        tool = PlaceOrderTool(actor=None, logged_in=False)
        reply = await tool.execute(sku="TOWEL-001")
        assert "登录" in reply

    async def test_place_order_unknown_sku(self, tmp_data):
        """未知 SKU → 明确提示核对。"""
        tool = PlaceOrderTool(actor="han", logged_in=True)
        reply = await tool.execute(sku="NOPE-999")
        assert "未找到商品" in reply

    async def test_place_order_over_stock(self, tmp_data):
        """数量超库存 → 拒绝下单（RUN-003 库存 18 件）。"""
        tool = PlaceOrderTool(actor="han", logged_in=True)
        reply = await tool.execute(sku="RUN-003", qty=99)
        assert "库存不足" in reply

    async def test_place_order_qty_over_99(self, tmp_data):
        """单笔数量上限 99 件 → 拒绝下单。"""
        tool = PlaceOrderTool(actor="han", logged_in=True)
        reply = await tool.execute(sku="TOWEL-001", qty=9999)
        assert "最多购买 99 件" in reply


# ---------- 签收前退款决策（纯函数） ----------

class TestPreReceiptPolicy:
    def test_p8_small_amount_auto_approve(self):
        """P8: 小额 + 账号正常 → 自动通过。"""
        decision = classify_pre_receipt_refund(
            {"total": 38, "status": "已付款"}, "C001"
        )
        assert decision.action == "auto_approve"
        assert decision.rule == "P8"

    def test_p5_over_amount_manual(self):
        """P5: 金额超 50 元上限 → 转人工。"""
        decision = classify_pre_receipt_refund(
            {"total": 478, "status": "已付款"}, "C001"
        )
        assert decision.action == "manual"
        assert decision.rule == "P5"

    def test_p0_switch_off(self, monkeypatch):
        """P0: 总开关关闭 → 转人工。"""
        monkeypatch.setattr(rp, "AUTO_APPROVE_ENABLED", False)
        decision = classify_pre_receipt_refund(
            {"total": 38, "status": "已发货"}, "C001"
        )
        assert decision.action == "manual"
        assert decision.rule == "P0"

    def test_p1_high_risk(self, monkeypatch):
        """P1: 高风险账号 → 转人工（小额也不例外）。"""
        monkeypatch.setattr(rp, "get_user_profile", lambda cid: {
            "customer_id": cid, "order_count": 10, "refund_count": 8,
            "refund_rate": 0.8, "recent_auto_approve": 0, "risk_score": 80,
        })
        decision = classify_pre_receipt_refund(
            {"total": 19, "status": "已付款"}, "C001"
        )
        assert decision.action == "manual"
        assert decision.rule == "P1"

    def test_p2_freq_exceeded(self, monkeypatch):
        """P2: 窗口内自动通过次数达上限 → 转人工。"""
        monkeypatch.setattr(rp, "get_user_profile", lambda cid: {
            "customer_id": cid, "order_count": 10, "refund_count": 1,
            "refund_rate": 0.1, "recent_auto_approve": 2, "risk_score": 20,
        })
        decision = classify_pre_receipt_refund(
            {"total": 19, "status": "已发货"}, "C001"
        )
        assert decision.action == "manual"
        assert decision.rule == "P2"


# ---------- approve_refund 签收前分支（工具集成） ----------

class TestApproveRefundPreReceipt:
    async def test_unshipped_small_auto_refund(self, tmp_data):
        """未发货小额（SO20260908011 ¥38）：自动审核直接退款，无物流拦截。"""
        tool = ApproveRefundTool(actor="han", logged_in=True)
        reply = await tool.execute(order_id="SO20260908011", reason="不想要")
        assert "尚未发货" in reply
        assert "自动审核通过" in reply and "退款已发起" in reply
        assert "拦截" not in reply
        assert _order("SO20260908011")["status"] == "已退款"
        records = rp.load_refund_records()
        assert any(r["order_id"] == "SO20260908011" for r in records)

    async def test_shipped_small_intercept_and_refund(self, tmp_data):
        """已发货小额（SO20260907012 ¥39）：自动拦截物流并退款，无需人工。"""
        tool = ApproveRefundTool(actor="han", logged_in=True)
        reply = await tool.execute(order_id="SO20260907012", reason="不想要")
        assert "已自动拦截物流并退款" in reply
        assert "无需任何操作" in reply
        assert "人工" not in reply
        assert _order("SO20260907012")["status"] == "已退款"
        traces = store.load_logistics()["SO20260907012"]
        assert traces[-1]["status"] == "已拦截，包裹退回商家"
        records = rp.load_refund_records()
        assert any(r["order_id"] == "SO20260907012" for r in records)

    async def test_over_50_manual_with_ticket(self, tmp_data):
        """大额（SO20260903004 ¥478）：转人工，创建工单，订单不退款。"""
        tool = ApproveRefundTool(actor="han", logged_in=True)
        n_tickets = len(store.load_tickets())
        reply = await tool.execute(order_id="SO20260903004", reason="不想要")
        # 「已提交人工审核」是 Gateway 触发后台人工提醒的检测标记
        assert "已提交人工审核" in reply
        assert "工单号" in reply
        assert _order("SO20260903004")["status"] == "已付款"   # 未自动退款
        assert len(store.load_tickets()) == n_tickets + 1      # 工单已创建

    async def test_refunded_order_not_repeat(self, tmp_data):
        """已退款订单重复申请 → 拒绝重复退款。"""
        tool = ApproveRefundTool(actor="han", logged_in=True)
        await tool.execute(order_id="SO20260908011", reason="不想要")
        reply = await tool.execute(order_id="SO20260908011", reason="不想要")
        assert "请勿重复申请" in reply


# ---------- gateway 转人工提醒（静态源码断言） ----------

class TestGatewayHumanAlert:
    def test_gateway_detects_manual_refund_marker(self):
        """gateway.py 必须检测「已提交人工审核」标记并进入人工接管模式。"""
        src = (V4_ROOT / "gateway.py").read_text(encoding="utf-8")
        assert '"已提交人工审核" in response' in src
        assert "takeover.activate" in src


# ---------- 前端鲜亮主题（静态断言） ----------

class TestBrightTheme:
    def test_index_html_bright_background(self):
        """index.html 背景改为鲜亮浅色系，配套文字/滚动条对比度。"""
        html = (V4_ROOT / "channels" / "web_ui" / "index.html").read_text(
            encoding="utf-8"
        )
        assert "--bg-primary: #eef4ff" in html        # 鲜亮浅蓝背景
        assert "--text-primary: #1e2a44" in html      # 深色文字保证对比度
        assert "--text-on-accent" in html             # 金色按钮深棕文字
        # 代码高亮主题同步换为浅色
        assert "github.min.css" in html
        assert "github-dark-dimmed" not in html

    def test_bot_reply_text_readable_on_light_bg(self):
        """机器人回复文字在浅色背景下可读：加粗为深色（非白色）、行内代码深金。"""
        html = (V4_ROOT / "channels" / "web_ui" / "index.html").read_text(
            encoding="utf-8"
        )
        # 加粗文字：深色主题遗留的 white 必须移除，改为深藏青
        assert ".msg-content strong" in html
        strong_block = html.split(".msg-content strong")[1][:200]
        assert "color: #101a30" in strong_block
        assert "color: white" not in strong_block
        # 行内代码：深金色（原 --accent 金色在浅底上对比不足）
        assert "color: #8a6320" in html
