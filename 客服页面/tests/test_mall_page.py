"""商品陈列页（/mall）与商品列表 API（/api/goods）测试。

不启动 uvicorn：手工构建 FastAPI 应用 + TestClient 直测路由层，验证：
- /mall 返回商品陈列页 HTML（含客服对话按钮，指向客服对话页）
- /api/goods 返回商品数组（经 store 数据后端，含 sku/price 字段）
- 对话页（/）机器人消息中的链接在新窗口打开（登录态不被跳走）
- 现有三端页面路由不受影响（回归）
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bus.queue import MessageBus
from channels.web import WebChannel


@pytest.fixture
def client(tmp_path, monkeypatch):
    """构建挂载了全部路由的 WebChannel 测试客户端。"""
    channel = WebChannel(
        MessageBus(), host="127.0.0.1", port=0,
        sessions_dir=str(tmp_path / "sessions"),
    )
    channel._app = FastAPI()
    channel._register_routes()
    return TestClient(channel._app)


class TestMallPage:
    def test_mall_page_served(self, client):
        """GET /mall → 200，返回商品陈列页 HTML。"""
        resp = client.get("/mall")
        assert resp.status_code == 200
        html = resp.text
        assert "商品陈列" in html

    def test_mall_page_has_service_button(self, client):
        """页面含「客服对话」按钮，跳转地址为客服对话页。

        按钮使用同源相对路径 "/"：无论经 127.0.0.1 还是 localhost 访问，
        跳转后 localStorage 中的登录态（nanoclaw_token 等）都在同一 origin 下，
        商城页登录 → 客服对话页自动登录才不会因跨域失效。
        """
        html = client.get("/mall").text
        assert "客服对话" in html
        assert 'href="/"' in html

    def test_mall_page_has_login_entry(self, client):
        """商城页增设登录入口：登录对话框 + 走 /api/auth/login 登录。"""
        html = client.get("/mall").text
        assert 'id="authArea"' in html          # 顶栏登录区域
        assert 'id="loginOverlay"' in html      # 登录对话框
        assert "/api/auth/login" in html        # REST 登录接口

    def test_mall_page_loads_goods_api(self, client):
        """页面 JS 从 /api/goods 拉取商品数据。"""
        html = client.get("/mall").text
        assert "/api/goods" in html


class TestChatPageLinks:
    def test_bot_links_open_in_new_window(self, client):
        """对话页机器人消息中的链接在新窗口打开（target=_blank）。

        修复：机器人推荐商品链接时，若在原窗口打开会把对话页跳走，
        返回时页面重载导致登录态丢失、需重新登录。新窗口打开则
        WebSocket 连接、聊天上下文与登录态全部保持原状。
        """
        html = client.get("/").text
        assert "marked.parse" in html            # 机器人消息经 Markdown 渲染
        assert "a.target = '_blank'" in html     # 渲染后链接统一补 target
        assert "noopener noreferrer" in html     # 且带 noopener（安全）

    def test_browse_goods_chip_opens_mall(self, client):
        """对话页「逛商品」快捷按钮跳转商城页（新窗口，保留对话态）。

        其余快捷按钮仍走 data-msg 发送预设消息；仅逛商品改为
        data-link="/mall" 页面跳转，新窗口打开避免跳走对话页。
        """
        html = client.get("/").text
        assert 'data-link="/mall"' in html          # 逛商品按钮指向商城
        assert 'data-msg="推荐几款 300 左右的跑鞋"' not in html  # 不再发消息
        assert "window.open(chip.dataset.link" in html  # 点击时新窗口打开

    def test_user_page_no_internal_entries(self, client):
        """用户端顶栏不暴露「客服端/管理端」入口（内部工作台不对顾客展示）。"""
        html = client.get("/").text
        assert ">客服端</a>" not in html
        assert ">管理端</a>" not in html

    def test_agent_page_no_user_entry(self, client):
        """客服工作台顶栏去掉「用户端」入口（保留管理端）。"""
        html = client.get("/agent").text
        assert ">用户端</a>" not in html
        assert ">管理端</a>" in html

    def test_identity_focus_on_current_question(self):
        """人设含「聚焦当前问题」规则：回答只围绕当前话题，
        不主动提及无关历史（买袜子时不汇报物流）。"""
        from pathlib import Path
        identity = (Path(__file__).resolve().parents[1]
                    / "identity.md").read_text(encoding="utf-8")
        assert "聚焦当前问题" in identity
        # 主动引导也须围绕当前话题，不引向历史话题
        assert "引导必须围绕顾客当前的话题" in identity

    def test_identity_tool_discipline_and_order_confirmation(self):
        """人设含「工具调用纪律」与「确认下单闭环」规则。

        修复场景：顾客说买袜子，机器人却同时查物流；顾客确认
        "好的下单"，机器人反问哪款商品。规则要求商品场景禁调
        物流工具、确认语直接关联讨论中的商品下单。
        """
        from pathlib import Path
        identity = (Path(__file__).resolve().parents[1]
                    / "identity.md").read_text(encoding="utf-8")
        assert "工具调用纪律" in identity
        # 商品场景严禁调用物流/订单工具
        assert "严禁同时调用 `query_logistics`" in identity
        assert "确认下单闭环" in identity
        assert "严禁反问" in identity

    def test_place_order_description_links_confirmation_to_context(self):
        """place_order 工具描述含确认关联上下文指引（'好的下单'直接下单）。"""
        from agent.tools.ecom.place_order import PlaceOrderTool
        desc = PlaceOrderTool.description.fget(PlaceOrderTool.__new__(PlaceOrderTool))
        assert "确认关联上下文" in desc
        assert "严禁再向顾客追问" in desc

    def test_query_logistics_description_guards_misuse(self):
        """query_logistics 工具描述含调用纪律（商品场景禁调，防惯性误调）。"""
        from agent.tools.ecom.query_logistics import QueryLogisticsTool
        desc = QueryLogisticsTool.description.fget(QueryLogisticsTool.__new__(QueryLogisticsTool))
        assert "严禁调用本工具" in desc


class TestGoodsApi:
    def test_api_goods_returns_list(self, client):
        """GET /api/goods → 200，返回商品数组（含 sku 与 price 字段）。"""
        resp = client.get("/api/goods")
        assert resp.status_code == 200
        goods = resp.json()
        assert isinstance(goods, list) and len(goods) > 0
        for g in goods:
            assert "sku" in g and "price" in g

    def test_existing_pages_unaffected(self, client):
        """现有三端页面路由回归：/、/agent、/admin 均正常。"""
        for path in ("/", "/agent", "/admin"):
            resp = client.get(path)
            assert resp.status_code == 200
