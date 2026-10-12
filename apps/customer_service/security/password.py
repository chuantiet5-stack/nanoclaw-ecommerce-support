"""密码哈希与校验（生产落地 P0-1.2 步骤 2）。

用户密码禁止明文存储，统一使用 bcrypt（自带盐、抗彩虹表）。
迁移方式见 migrations/migrate_json_to_mysql.py（明文 → bcrypt 哈希）。
"""

import bcrypt


def hash_password(plain_password: str) -> str:
    """生成 bcrypt 哈希（含 salt）。"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码（恒定时间比较，防时序攻击）。"""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False
