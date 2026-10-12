"""FastAPI 鉴权依赖（生产落地 P0-1.2 步骤 3）。

供管理端 API 加鉴权时使用（FastAPI Depends 注入）：

    from fastapi import Depends
    from security.deps import get_current_user, get_current_admin

    @app.get("/api/sessions")
    async def api_sessions(user: dict = Depends(get_current_admin)):
        ...

注意：当前 Web 渠道的演示 API 尚未接入（接入会破坏现有三端前端，
属于高风险改造，待前端支持携带 Token 后再启用）。
"""

from typing import Optional

from fastapi import Header, HTTPException, status, WebSocket

from security.jwt import decode_access_token


async def get_current_user(authorization: str = Header(None)) -> dict:
    """从 Authorization: Bearer <token> 解析当前用户。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
        )
    token = authorization.removeprefix("Bearer ")
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
        )
    return payload


async def get_current_admin(authorization: str = Header(None)) -> dict:
    """管理员鉴权（在用户鉴权基础上检查 role）。"""
    user = await get_current_user(authorization)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


async def get_ws_user(websocket: WebSocket) -> Optional[dict]:
    """WebSocket 连接时从 query 参数或 header 取 token（游客返回 None）。"""
    token = websocket.query_params.get("token") or ""
    if not token:
        auth = websocket.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ")
    return decode_access_token(token) if token else None
