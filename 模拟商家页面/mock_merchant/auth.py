"""演示级 Token 鉴权：登录签发 / 刷新 / 校验 / 角色权限。

Token 为本地可验证的随机令牌（存 SQLite，含角色、商家 ID、员工/顾客 ID、
过期时间与权限范围），完全模拟真实商家 API 的 Bearer Token 体验。

角色与权限范围：
- merchant_admin 商家管理员：全部权限（*）
- operator        商家运营  ：商品/库存/订单/物流/看板等
- after_sales     售后客服  ：售后/退款/工单/顾客（只读）/看板
- customer        顾客      ：仅本人数据（订单/售后/退款/工单）+ 商品只读
"""

from datetime import datetime, timedelta

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config
from mock_merchant import db, rules
from mock_merchant.response import AppError

# 角色 → 权限范围
ROLE_SCOPES = {
    "merchant_admin": ["*"],
    "operator": [
        "products:read", "products:write",
        "inventory:read", "inventory:write",
        "orders:read", "orders:write",
        "logistics:read", "logistics:write",
        "customers:read",
        "tickets:read", "tickets:write", "tickets:create",
        "aftersales:read", "aftersales:create",
        "refunds:read", "refunds:create",
        "dashboard:read",
        "faults:read",
    ],
    "after_sales": [
        "products:read",
        "orders:read",
        "logistics:read",
        "customers:read",
        "tickets:read", "tickets:write", "tickets:create",
        "aftersales:read", "aftersales:write", "aftersales:create",
        "refunds:read", "refunds:write", "refunds:create",
        "dashboard:read",
    ],
    "customer": [
        "products:read",
        "orders:read",
        "logistics:read",
        "customers:read",
        "aftersales:create", "aftersales:read",
        "refunds:create", "refunds:read",
        "tickets:create", "tickets:read",
    ],
}

_bearer = HTTPBearer(auto_error=False)


def issue_token(subject_type: str, subject_id: str, username: str, role: str,
                merchant_id: str = "M001") -> dict:
    """签发新 Token（演示级随机令牌，落库可验证）。"""
    token = rules.new_token()
    expires = datetime.now() + timedelta(hours=config.TOKEN_TTL_HOURS)
    created = rules.now_str()
    scope = ",".join(ROLE_SCOPES.get(role, []))
    db.execute(
        """INSERT INTO tokens (token, subject_type, subject_id, username, role, merchant_id,
           scope, expires_at, revoked, created_at) VALUES (?,?,?,?,?,?,?,?,0,?)""",
        (token, subject_type, subject_id, username, role, merchant_id,
         scope, expires.strftime("%Y-%m-%d %H:%M:%S"), created),
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_in": config.TOKEN_TTL_HOURS * 3600,
        "role": role,
        "merchant_id": merchant_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "username": username,
        "scope": ROLE_SCOPES.get(role, []),
    }


def verify_password(plain: str, hashed: str) -> bool:
    from mock_merchant.seed import hash_password
    return hash_password(plain or "") == hashed


def authenticate(username: str, password: str) -> dict:
    """校验商家员工或顾客账号，返回身份信息；失败抛 AppError。"""
    staff = db.query_one(
        "SELECT * FROM staffs WHERE username = ? AND status = '在职'", (username,)
    )
    if staff:
        if not verify_password(password, staff["password_hash"]):
            raise AppError("用户名或密码错误", "AUTH_FAILED", 401)
        return {
            "subject_type": "staff",
            "subject_id": staff["staff_id"],
            "username": staff["username"],
            "name": staff["name"],
            "role": staff["role"],
            "merchant_id": staff["merchant_id"],
        }
    customer = db.query_one("SELECT * FROM customers WHERE username = ?", (username,))
    if customer:
        if not verify_password(password, customer["password_hash"]):
            raise AppError("用户名或密码错误", "AUTH_FAILED", 401)
        return {
            "subject_type": "customer",
            "subject_id": customer["customer_id"],
            "username": customer["username"],
            "name": customer["name"],
            "role": "customer",
            "merchant_id": "M001",
        }
    raise AppError("用户名或密码错误", "AUTH_FAILED", 401)


def resolve_token(token: str) -> dict:
    """解析并校验 Token，返回主体信息（principal）。"""
    if not token:
        raise AppError("未登录或 Token 无效，请先调用 /auth/token 获取令牌",
                       "UNAUTHORIZED", 401)
    row = db.query_one("SELECT * FROM tokens WHERE token = ?", (token,))
    if not row or row["revoked"]:
        raise AppError("Token 无效或已吊销，请重新登录", "UNAUTHORIZED", 401)
    try:
        expired = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.now()
    except Exception:
        expired = True
    if expired:
        raise AppError("Token 已过期，请刷新或重新登录", "TOKEN_EXPIRED", 401)
    return {
        "type": row["subject_type"],          # staff / customer
        "subject_id": row["subject_id"],      # 员工 ID 或顾客 ID
        "username": row["username"],
        "role": row["role"],
        "merchant_id": row["merchant_id"],
        "scope": row["scope"].split(",") if row["scope"] else [],
        "expires_at": row["expires_at"],
        "token": token,
    }


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """FastAPI 依赖：要求有效 Bearer Token。"""
    if credentials is None:
        raise AppError("未登录或 Token 无效，请先调用 /auth/token 获取令牌",
                       "UNAUTHORIZED", 401)
    return resolve_token(credentials.credentials)


def require_scope(scope: str):
    """FastAPI 依赖工厂：要求指定权限范围（* 通配全部）。"""

    def _dep(principal: dict = Depends(current_principal)) -> dict:
        scopes = principal.get("scope", [])
        if "*" not in scopes and scope not in scopes:
            raise AppError(
                f"当前角色（{principal.get('role')}）无权执行该操作，需要权限 {scope}",
                "FORBIDDEN", 403,
            )
        return principal

    return _dep


def require_staff(principal: dict = Depends(current_principal)) -> dict:
    """要求商家员工身份（顾客 Token 拒绝）。"""
    if principal["type"] != "staff":
        raise AppError("该接口仅限商家员工调用", "FORBIDDEN", 403)
    return principal


def check_customer_owner(principal: dict, customer_id: str) -> None:
    """顾客 Token 只能访问自己的数据；商家员工不受限。"""
    if principal["type"] == "customer" and principal["subject_id"] != customer_id:
        raise AppError("无权访问其他顾客的数据", "FORBIDDEN", 403)
