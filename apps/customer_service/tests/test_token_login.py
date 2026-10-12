"""商城页登录态跨页共享：WebSocket token 免密登录测试。

场景：顾客在商城页（/mall）通过 REST /api/auth/login 登录并保存 JWT，
点击「客服对话」跳转到对话页后，前端凭 token 发送
{"type": "login", "token": ...} 自动恢复登录，无需再次输入密码。

覆盖：
- 有效 token → login_ok（返回用户名/昵称/角色，登录态生效）
- 无效/伪造 token → login_fail
- 过期 token → login_fail
- 原有账号密码登录不受影响（回归）
- 登出后登录态清除（回归）
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bus.queue import MessageBus
from channels.web import WebChannel


@pytest.fixture
def ws_client(tmp_path):
    """挂载全部路由的测试客户端（含 /ws WebSocket 端点）。"""
    channel = WebChannel(
        MessageBus(), host="127.0.0.1", port=0,
        sessions_dir=str(tmp_path / "sessions"),
    )
    channel._app = FastAPI()
    channel._register_routes()
    return TestClient(channel._app)


def _rest_login(client, username, password):
    return client.post("/api/auth/login", json={
        "username": username, "password": password,
    })


class TestTokenLogin:
    def test_valid_token_login_ok(self, ws_client):
        """有效 token 免密登录：返回 login_ok 与用户档案。"""
        token = _rest_login(ws_client, "han", "123456").json()["token"]
        with ws_client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "login", "token": token}))
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "login_ok"
            assert reply["username"] == "han"
            assert reply["name"] == "韩梅梅"
            assert reply["role"] == "user"
            assert reply["token"] == token

    def test_invalid_token_login_fail(self, ws_client):
        """伪造 token → login_fail。"""
        with ws_client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "login", "token": "fake.token.here"}))
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "login_fail"

    def test_expired_token_login_fail(self, ws_client):
        """过期 token（exp 已过）→ login_fail。"""
        from security.jwt import create_access_token
        expired = create_access_token(
            {"sub": "han", "role": "user"},
            expires_delta=timedelta(seconds=-3600),  # 1 小时前过期
        )
        with ws_client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "login", "token": expired}))
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "login_fail"

    def test_password_login_still_works(self, ws_client):
        """回归：账号密码登录（无 token）行为不变。"""
        with ws_client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "login", "username": "han", "password": "123456",
            }))
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "login_ok"
            assert reply["username"] == "han"

    def test_token_login_then_logout(self, ws_client):
        """token 登录后登出：登录态清除，可再走账号密码登录。"""
        token = _rest_login(ws_client, "li", "123456").json()["token"]
        with ws_client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "login", "token": token}))
            assert json.loads(ws.receive_text())["type"] == "login_ok"

            ws.send_text(json.dumps({"type": "logout"}))
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "logout_ok"

            # 登出后仍可用账号密码重新登录（互不干扰）
            ws.send_text(json.dumps({
                "type": "login", "username": "li", "password": "123456",
            }))
            assert json.loads(ws.receive_text())["type"] == "login_ok"
