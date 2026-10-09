"""基于 Redis 的会话管理器（生产落地 P0-1.1 步骤 7）。

接口与 session/manager.py（JSONL 文件版）保持一致：
save_message / get_history / clear / list_sessions，
便于平滑替换（替换动作属于高风险改造，待 Redis 就绪后进行）。

差异：Redis List 天然原子（RPUSH），并发写不会互相覆盖；
每次写入刷新 TTL（默认 7 天过期），自动清理僵尸会话。
"""

import json
from datetime import datetime, timedelta

from db.database import redis_client


class RedisSessionManager:
    SESSION_TTL = timedelta(days=7)  # 会话 7 天过期

    def __init__(self, prefix: str = "session:"):
        self.prefix = prefix

    def _key(self, session_key: str) -> str:
        return f"{self.prefix}{session_key}"

    async def save_message(self, session_key: str, message: dict) -> None:
        """保存单条消息到 Redis List。"""
        message = dict(message)
        message["timestamp"] = datetime.now().isoformat()
        key = self._key(session_key)
        # RPUSH 追加到列表尾部（原子操作）
        await redis_client.rpush(key, json.dumps(message, ensure_ascii=False))
        # 每次写入刷新过期时间
        await redis_client.expire(key, int(self.SESSION_TTL.total_seconds()))

    async def get_history(self, session_key: str) -> list[dict]:
        """加载全部历史消息。"""
        key = self._key(session_key)
        raw_list = await redis_client.lrange(key, 0, -1)
        history = []
        for raw in raw_list:
            try:
                msg = json.loads(raw)
                msg.pop("timestamp", None)
                history.append(msg)
            except json.JSONDecodeError:
                continue
        return history

    async def clear(self, session_key: str) -> None:
        await redis_client.delete(self._key(session_key))

    async def list_sessions(self) -> list[str]:
        """列出所有活跃会话 key。"""
        keys = await redis_client.keys(f"{self.prefix}*")
        return [k.removeprefix(self.prefix) for k in keys]
