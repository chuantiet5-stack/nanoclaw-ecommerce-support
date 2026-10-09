"""统一登录认证服务（生产落地 P0-1.2 步骤 4）。

三端登录（用户端 WebSocket login / 客服端 / 管理端 REST 登录）统一走
本模块的 authenticate()，替代各处直接读 users.json 比对明文密码：

- JSON 模式（默认）：读 data/users.json
    * 密钥为 bcrypt 哈希（$2b$ 前缀）→ verify_password 校验
    * 兼容存量明文密码：比对成功后自动升级为 bcrypt 哈希写回文件
- MySQL 模式（DATA_BACKEND=mysql）：经 db.sync_bridge 查询 users 表
    * 密码统一为 bcrypt 哈希（由迁移脚本保证）

登录成功返回用户档案 dict（含 role），失败返回 None——不区分
"用户不存在"与"密码错误"，避免账号枚举。
"""

import json
import os
from pathlib import Path

from security.jwt import create_access_token
from security.password import verify_password, hash_password

# 用户数据文件（JSON 模式）
USERS_FILE = Path(__file__).resolve().parents[1] / "data" / "users.json"

# 数据后端开关（与 agent/tools/ecom/store.py 一致）
DATA_BACKEND = os.getenv("DATA_BACKEND", "json").lower()


def _load_users_json() -> dict:
    """读取 users.json（损坏时返回空表）。"""
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"users": []}


def _save_users_json(data: dict) -> None:
    """写回 users.json（失败静默，仅影响明文升级）。"""
    try:
        USERS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
        )
    except Exception:
        pass


def _mysql_user_dict(row) -> dict:
    """将 db.models.User 行转为与 JSON 档案同构的 dict。"""
    return {
        "username": row.username,
        "customer_id": row.customer_id,
        "name": row.name,
        "level": getattr(row, "level", "普通会员"),
        "points": getattr(row, "points", 0),
        "role": getattr(row, "role", "user"),
    }


def authenticate(username: str, password: str) -> dict | None:
    """校验账号密码，成功返回用户档案（含 role），失败返回 None。

    兼容两种密码存储：
    - bcrypt 哈希（$2b$/$2a$ 前缀）→ 恒定时间校验
    - 存量明文 → 比对成功后自动升级为 bcrypt 哈希写回（仅 JSON 模式）
    """
    if not username or not password:
        return None

    if DATA_BACKEND == "mysql":
        # MySQL 模式：经同步桥查询 users 表
        from db.sync_bridge import run_async
        from db.repository import get_user_by_username

        row = run_async(get_user_by_username(username))
        if row is None:
            return None
        if not verify_password(password, row.password_hash):
            return None
        return _mysql_user_dict(row)

    # JSON 模式
    data = _load_users_json()
    user = next(
        (u for u in data.get("users", []) if u.get("username") == username), None
    )
    if user is None:
        return None

    stored = str(user.get("password", ""))
    if stored.startswith(("$2b$", "$2a$")):
        # 已是 bcrypt 哈希
        if verify_password(password, stored):
            return dict(user)
        return None

    # 存量明文密码：比对成功后自动升级为 bcrypt
    if stored == password:
        user["password"] = hash_password(password)
        _save_users_json(data)
        return dict(user)
    return None


def issue_token(user: dict) -> str:
    """为通过认证的用户签发 JWT。"""
    return create_access_token({
        "sub": user["username"],
        "customer_id": user.get("customer_id", ""),
        "role": user.get("role", "user"),
        "name": user.get("name", ""),
    })
