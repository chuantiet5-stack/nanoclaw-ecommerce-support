"""安全模块测试（生产落地 P2-3.1）：密码哈希 + JWT + FastAPI 鉴权依赖。"""

from datetime import timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from security.deps import get_current_admin, get_current_user
from security.jwt import create_access_token, decode_access_token
from security.password import hash_password, verify_password


class TestPassword:
    def test_hash_and_verify(self):
        hashed = hash_password("123456")
        assert hashed != "123456"
        assert hashed.startswith("$2b$")  # bcrypt 格式
        assert verify_password("123456", hashed) is True

    def test_wrong_password(self):
        hashed = hash_password("123456")
        assert verify_password("wrong", hashed) is False

    def test_hash_unique_salt(self):
        """每次哈希使用随机盐，同一密码两次哈希结果不同。"""
        assert hash_password("abc") != hash_password("abc")

    def test_verify_invalid_hash(self):
        assert verify_password("abc", "not-a-bcrypt-hash") is False


class TestJWT:
    def test_round_trip(self):
        token = create_access_token({"sub": "han", "role": "user"})
        payload = decode_access_token(token)
        assert payload["sub"] == "han"
        assert payload["role"] == "user"

    def test_expired_token(self):
        token = create_access_token(
            {"sub": "han"}, expires_delta=timedelta(seconds=-1)
        )
        assert decode_access_token(token) is None

    def test_invalid_token(self):
        assert decode_access_token("garbage.token.value") is None
        assert decode_access_token("") is None

    def test_tampered_token(self):
        token = create_access_token({"sub": "han", "role": "user"})
        # 篡改 payload 部分后签名校验失败
        assert decode_access_token(token[:-2] + "xx") is None


class TestFastAPIDeps:
    """模拟管理端 API：验证 401/403 鉴权链路。"""

    @staticmethod
    def _make_app() -> FastAPI:
        app = FastAPI()

        @app.get("/api/user-only")
        async def user_only(user: dict = Depends(get_current_user)):
            return {"username": user.get("sub")}

        @app.get("/api/admin-only")
        async def admin_only(user: dict = Depends(get_current_admin)):
            return {"username": user.get("sub")}

        return app

    def test_no_token_401(self):
        client = TestClient(self._make_app())
        resp = client.get("/api/user-only")
        assert resp.status_code == 401

    def test_bad_token_401(self):
        client = TestClient(self._make_app())
        resp = client.get(
            "/api/user-only", headers={"Authorization": "Bearer invalid"}
        )
        assert resp.status_code == 401

    def test_valid_token_200(self):
        client = TestClient(self._make_app())
        token = create_access_token({"sub": "han", "role": "user"})
        resp = client.get(
            "/api/user-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"username": "han"}

    def test_non_admin_403(self):
        client = TestClient(self._make_app())
        token = create_access_token({"sub": "han", "role": "user"})
        resp = client.get(
            "/api/admin-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403

    def test_admin_200(self):
        client = TestClient(self._make_app())
        token = create_access_token({"sub": "admin", "role": "admin"})
        resp = client.get(
            "/api/admin-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
