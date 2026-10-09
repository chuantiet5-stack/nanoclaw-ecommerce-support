"""全局限流器测试（生产落地 P1-2.2）。"""

import asyncio
import time

from providers.rate_limiter import TokenBucket


class TestTokenBucket:
    async def test_burst_within_capacity(self):
        """容量内的请求立即通过，不等待。"""
        bucket = TokenBucket(rate=10, capacity=20)
        start = time.monotonic()
        for _ in range(20):
            await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5  # 20 个突发请求应几乎瞬时完成

    async def test_refill_rate(self):
        """桶空后需按速率等待补充。"""
        bucket = TokenBucket(rate=10, capacity=2)
        await bucket.acquire()
        await bucket.acquire()  # 桶已空
        start = time.monotonic()
        await bucket.acquire()  # 需等待约 0.1s（rate=10/s）
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05  # 必须发生了等待

    async def test_concurrent_acquire_all_succeed(self):
        """并发场景下所有请求最终都能获取 token（无死锁、无遗漏）。"""
        bucket = TokenBucket(rate=100, capacity=10)

        async def worker():
            await bucket.acquire()
            return True

        results = await asyncio.wait_for(
            asyncio.gather(*[worker() for _ in range(50)]), timeout=10
        )
        assert all(results)
        assert len(results) == 50

    async def test_rate_limit_never_exceeds_steady_rate(self):
        """稳态下实际 QPS 不超过配置速率（抽样验证）。"""
        rate = 20
        bucket = TokenBucket(rate=rate, capacity=1)
        await bucket.acquire()  # 用掉突发容量
        start = time.monotonic()
        for _ in range(5):
            await bucket.acquire()
        elapsed = time.monotonic() - start
        # 5 个请求至少需要 4/rate 秒（第一个也要等补充）
        assert elapsed >= 4 / rate * 0.8  # 留 20% 误差容忍
