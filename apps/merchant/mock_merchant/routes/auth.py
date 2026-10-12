"""鉴权 API：获取 Token / 刷新 Token / 当前身份。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from mock_merchant import auth
from mock_merchant import db
from mock_merchant.response import AppError, ok

router = APIRouter(prefix="/auth", tags=["鉴权"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/token")
def login(body: LoginRequest):
    """演示账号登录，返回模拟 Bearer Token。

    商家员工：merchant_admin / 123456、operator / 123456、after_sales / 123456
    顾客账号：han / 123456（C001 韩梅梅）、lei / 123456（C002 李雷）
    """
    identity = auth.authenticate(body.username, body.password)
    token = auth.issue_token(
        identity["subject_type"], identity["subject_id"], identity["username"],
        identity["role"], identity["merchant_id"],
    )
    token["name"] = identity["name"]
    return ok(token, "登录成功")


@router.post("/refresh")
def refresh(principal: dict = Depends(auth.current_principal)):
    """刷新 Token（旧 Token 立即吊销）。"""
    db.execute("UPDATE tokens SET revoked = 1 WHERE token = ?", (principal["token"],))
    token = auth.issue_token(
        principal["type"], principal["subject_id"], principal["username"],
        principal["role"], principal["merchant_id"],
    )
    return ok(token, "Token 已刷新")


@router.get("/me")
def me(principal: dict = Depends(auth.current_principal)):
    """当前登录身份（角色 / 权限范围 / 过期时间）。"""
    name = principal["username"]
    if principal["type"] == "staff":
        row = db.query_one("SELECT name FROM staffs WHERE staff_id = ?",
                           (principal["subject_id"],))
    else:
        row = db.query_one("SELECT name FROM customers WHERE customer_id = ?",
                           (principal["subject_id"],))
    if row:
        name = row["name"]
    return ok({
        "subject_type": principal["type"],
        "subject_id": principal["subject_id"],
        "username": principal["username"],
        "name": name,
        "role": principal["role"],
        "merchant_id": principal["merchant_id"],
        "scope": principal["scope"],
        "expires_at": principal["expires_at"],
    })
