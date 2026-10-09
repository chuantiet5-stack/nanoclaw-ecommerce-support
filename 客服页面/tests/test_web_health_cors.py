"""WebChannel /health 端点与 CORS 中间件测试（生产落地 P0-1.2/3.4）。

不启动 uvicorn：手工构建 FastAPI 应用 + TestClient 直测路由层，
验证：
- /health 探活端点可用（无鉴权、轻量）
- CORS 仅放行 CORS_ORIGINS 指定的来源（默认 http://localhost:8080）
- 同源普通请求不受 CORS 影响（现有功能回归）
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bus.queue import MessageBus
from channels.web import WebChannel


@pytest.fixture
def client(tmp_path, monkeypatch):
    """构建挂载了全部路由的 WebChannel 测试客户端。"""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:8080")
    channel = WebChannel(
        MessageBus(), host="127.0.0.1", port=0,
        sessions_dir=str(tmp_path / "sessions"),
    )
    channel._app = FastAPI()
    channel._register_routes()
    return TestClient(channel._app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        """GET /health → 200 {"status": "ok", "timestamp": ...}"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data


class TestCORSMiddleware:
    def test_preflight_allowed_origin(self, client):
        """允许列表内来源的预检请求 → 放行并回显 Origin。"""
        resp = client.options(
            "/api/sessions",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:8080"

    def test_preflight_disallowed_origin_rejected(self, client):
        """非允许来源的预检请求 → 拒绝（不回显 Origin）。"""
        resp = client.options(
            "/api/sessions",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 400
        assert "access-control-allow-origin" not in resp.headers

    def test_allowed_origin_actual_request(self, client):
        """允许来源的实际 GET → 响应携带 CORS 头。"""
        resp = client.get(
            "/api/sessions", headers={"Origin": "http://localhost:8080"}
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:8080"

    def test_same_origin_get_unaffected(self, client):
        """无 Origin 头的同源请求不受 CORS 影响（现有功能回归）。"""
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_existing_api_still_works(self, client):
        """现有 API（/api/config）在同源下正常返回（回归）。"""
        resp = client.get("/api/config")
        assert resp.status_code == 200
        assert "autoRefundEnabled" in resp.json()
