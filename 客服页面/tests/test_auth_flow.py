"""JWT 鉴权接线测试（生产落地 P0-1.2 步骤 4）。

覆盖：
- authenticate()：bcrypt 校验 / 错误密码 / 存量明文自动升级
- POST /api/auth/login：签发 token / 错误凭证 401
- 鉴权中间件（REQUIRE_API_AUTH 门控）：
  * 默认关闭：无 token 访问 API 正常（回归，接口不变）
  * 开启：无 token 401 / 伪 token 401 / 普通用户 403 / 管理员 200
  * 豁免路径：/api/auth/login、/health、CORS 预检 OPTIONS
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bus.queue import MessageBus
from channels.web import WebChannel
from security import auth_service
from security.auth_service import authenticate, issue_token


@pytest.fixture
def client(tmp_path, monkeypatch):
    """默认模式（REQUIRE_API_AUTH=false）的测试客户端。"""
    monkeypatch.delenv("REQUIRE_API_AUTH", raising=False)
    channel = WebChannel(
        MessageBus(), host="127.0.0.1", port=0,
        sessions_dir=str(tmp_path / "sessions"),
    )
    channel._app = FastAPI()
    channel._register_routes()
    return TestClient(channel._app)


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    """开启鉴权模式（REQUIRE_API_AUTH=true）的测试客户端。"""
    monkeypatch.setenv("REQUIRE_API_AUTH", "true")
    channel = WebChannel(
        MessageBus(), host="127.0.0.1", port=0,
        sessions_dir=str(tmp_path / "sessions"),
    )
    channel._app = FastAPI()
    channel._register_routes()
    return TestClient(channel._app)


class TestAuthenticate:
    def test_bcrypt_user_login(self):
        """users.json 已存 bcrypt 哈希：han/123456 登录成功，含角色。"""
        user = authenticate("han", "123456")
        assert user is not None
        assert user["username"] == "han"
        assert user["role"] == "user"
        assert user["customer_id"] == "C001"

    def test_admin_login(self):
        """管理员账号：admin/admin123456 → role=admin。"""
        user = authenticate("admin", "admin123456")
        assert user is not None
        assert user["role"] == "admin"

    def test_wrong_password(self):
        assert authenticate("han", "wrong-password") is None

    def test_unknown_user(self):
        assert authenticate("nobody", "123456") is None

    def test_legacy_plaintext_auto_upgrade(self, tmp_path, monkeypatch):
        """存量明文密码：登录成功且自动升级为 bcrypt 哈希写回文件。"""
        users_file = tmp_path / "users.json"
        users_file.write_text(json.dumps({
            "default_demo_user": "legacy",
            "users": [{
                "username": "legacy", "password": "plain123",
                "customer_id": "C777", "name": "老用户",
                "level": "普通会员", "points": 0, "role": "user",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(auth_service, "USERS_FILE", users_file)

        # 明文登录成功
        user = authenticate("legacy", "plain123")
        assert user is not None

        # 文件中密码已升级为 bcrypt 哈希
        data = json.loads(users_file.read_text(encoding="utf-8"))
        stored = data["users"][0]["password"]
        assert stored.startswith("$2b$")
        assert stored != "plain123"

        # 升级后旧明文密码仍可登录（走 bcrypt 校验），错误密码被拒
        assert authenticate("legacy", "plain123") is not None
        assert authenticate("legacy", "bad") is None


class TestRestLogin:
    def test_login_ok_returns_token(self, client):
        resp = client.post("/api/auth/login", json={
            "username": "admin", "password": "admin123456",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["role"] == "admin"
        assert data["token"]

    def test_login_wrong_password_401(self, client):
        resp = client.post("/api/auth/login", json={
            "username": "han", "password": "nope",
        })
        assert resp.status_code == 401
        assert resp.json()["ok"] is False

    def test_token_is_valid_jwt(self, client):
        """登录返回的 token 可被 decode 校验（与 WS login_ok 中签发的一致）。"""
        from security.jwt import decode_access_token
        resp = client.post("/api/auth/login", json={
            "username": "han", "password": "123456",
        })
        token = resp.json()["token"]
        payload = decode_access_token(token)
        assert payload["sub"] == "han"
        assert payload["role"] == "user"


class TestAuthMiddlewareOff:
    """默认模式回归：鉴权关闭时所有 API 无需 token（接口不变）。"""

    def test_api_open_without_token(self, client):
        assert client.get("/api/sessions").status_code == 200
        assert client.get("/api/stats").status_code == 200

    def test_health_open(self, client):
        assert client.get("/health").status_code == 200


class TestAuthMiddlewareOn:
    """开启模式：按指南 1.2.3 验证方法逐条核对。"""

    def test_no_token_401(self, auth_client):
        resp = auth_client.get("/api/stats")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "未提供认证令牌"

    def test_invalid_token_401(self, auth_client):
        resp = auth_client.get(
            "/api/stats", headers={"Authorization": "Bearer invalid.token.here"}
        )
        assert resp.status_code == 401
        assert "令牌无效" in resp.json()["detail"]

    def test_normal_user_403(self, auth_client):
        """普通用户（han）访问管理 API → 403（指南验证方法 2）。"""
        from security.jwt import create_access_token
        token = create_access_token({"sub": "han", "role": "user", "customer_id": "C001"})
        resp = auth_client.get(
            "/api/sessions", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
        assert "管理员" in resp.json()["detail"]

    def test_admin_token_200(self, auth_client):
        from security.jwt import create_access_token
        token = create_access_token({"sub": "admin", "role": "admin", "customer_id": "C999"})
        for path in ("/api/sessions", "/api/stats", "/api/takeovers"):
            resp = auth_client.get(path, headers={
                "Authorization": f"Bearer {token}"
            })
            assert resp.status_code == 200, f"{path} 应 200，实际 {resp.status_code}"

    def test_login_endpoint_exempt(self, auth_client):
        """豁免路径：登录端点自身无需 token。"""
        resp = auth_client.post("/api/auth/login", json={
            "username": "admin", "password": "admin123456",
        })
        assert resp.status_code == 200

    def test_health_exempt(self, auth_client):
        """/health 不在 /api 前缀下，探活不受鉴权影响。"""
        assert auth_client.get("/health").status_code == 200

    def test_cors_preflight_passes_auth(self, auth_client):
        """CORS 预检（OPTIONS）放行：不会被鉴权中间件 401。"""
        resp = auth_client.options(
            "/api/sessions",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8080"
