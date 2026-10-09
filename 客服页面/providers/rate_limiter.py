"""全局限流器：防止 LLM API 调用超配额（生产落地 P1-2.2 步骤 2）。

Token Bucket 令牌桶算法：
- rate：每秒生成的 token 数（稳态 QPS）
- capacity：桶容量（最大突发请求数）

由 agent/loop.py 在每次调用 LLM 前 acquire()，超出配额的请求自动排队等待，
避免高峰期打爆 API 配额。速率可通过环境变量调整：
- LLM_RATE_QPS：每秒请求数，默认 10
- LLM_RATE_BURST：突发容量，默认 20
"""

import asyncio
import os
import time


class TokenBucket:
    """令牌桶限流器（异步安全）。"""

    def __init__(self, rate: float, capacity: float):
        """
        Args:
            rate: 每秒生成的 token 数（QPS）
            capacity: 桶容量（最大突发请求数）
        """
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个 token，不够则等待（持锁等待，保证排队公平）。"""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_refill = now

                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                # 计算需要等待多久才有 1 个 token
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)


# 全局限流器实例（按实际 API 配额通过环境变量调整）
llm_limiter = TokenBucket(
    rate=float(os.getenv("LLM_RATE_QPS", "10")),
    capacity=float(os.getenv("LLM_RATE_BURST", "20")),
)
