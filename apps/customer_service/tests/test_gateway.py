"""Gateway 并发消费 / 会话串行 / 超时 / 错误隔离 / LRU 测试
（生产落地 P0-1.3 + P1-2.1）。

使用 FakeAgent / FakeChannel，不依赖 LLM 与真实渠道。
"""

import asyncio

import pytest

from bus.queue import InboundMessage, MessageBus
from gateway import Gateway


class FakeSessionManager:
    def __init__(self):
        self.saved = []

    def save_message(self, session_key, message):
        self.saved.append((session_key, message))

    def get_history(self, session_key):
        return []


class FakeTools:
    async def execute(self, name, args):
        if name == "escalate_to_human":
            return "已为您转接人工客服，工单号：TK20260907000001，客服会尽快联系您。"
        return "ok"


class FakeAgent:
    """离线 Agent 替身：可配置延迟/失败/记录处理顺序。"""

    def __init__(self, session_key, delay=0.0, fail=False):
        self.session_key = session_key
        self.delay = delay
        self.fail = fail
        self.provider = None  # _generate_suggestion 直接跳过
        self.session_manager = FakeSessionManager()
        self.tools = FakeTools()
        self.processed = []

    async def run(self, content, persist_user_message=True, on_event=None):
        # on_event：对齐真实 AgentLoop.run 的流式回调签名（网关会传入）
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        self.processed.append(content)
        return f"echo:{content}"


class FakeChannel:
    def __init__(self, name="test"):
        self.name = name
        self.sent = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send(self, message):
        self.sent.append(message)


def make_gateway(agent_configs=None):
    """构造测试用 Gateway。agent_configs: {session_key: {"delay":.., "fail":..}}"""
    bus = MessageBus()
    created = []

    def factory(session_key):
        cfg = (agent_configs or {}).get(session_key, {})
        created.append(session_key)
        return FakeAgent(session_key, **cfg)

    gateway = Gateway(bus, [FakeChannel("test")], factory)
    return gateway, created


def _msg(sender: str, content: str, channel: str = "test") -> InboundMessage:
    return InboundMessage(
        channel=channel, sender_id=sender, chat_id=f"chat-{sender}", content=content
    )


async def drive(gateway, messages, expect, timeout=10):
    """启动消费循环 → 发布消息 → 收集 expect 条出站消息 → 停止。"""
    proc = asyncio.create_task(gateway._process_inbound())
    for m in messages:
        await gateway.bus.publish_inbound(m)
    outbound = []
    try:
        while len(outbound) < expect:
            outbound.append(
                await asyncio.wait_for(
                    gateway.bus.consume_outbound(), timeout=timeout
                )
            )
    except asyncio.TimeoutError:
        pass
    proc.cancel()
    await asyncio.gather(proc, return_exceptions=True)
    return outbound


class TestConcurrency:
    async def test_cross_session_concurrent(self):
        """跨会话并发：3 个会话各 0.3s，总耗时应远小于串行的 0.9s。"""
        configs = {f"test:s{i}": {"delay": 0.3} for i in range(3)}
        gateway, _ = make_gateway(configs)

        import time
        start = time.monotonic()
        outbound = await drive(
            gateway, [_msg(f"s{i}", f"m{i}") for i in range(3)], expect=3
        )
        elapsed = time.monotonic() - start

        assert len(outbound) == 3
        assert elapsed < 0.7  # 串行需要 0.9s，并发约 0.3s

    async def test_same_session_strict_ordering(self):
        """同一会话内消息严格按序处理（保持原串行语义）。"""
        gateway, _ = make_gateway()
        outbound = await drive(
            gateway, [_msg("s1", f"m{i}") for i in range(5)], expect=5
        )
        contents = [o.content for o in outbound]
        assert contents == [f"echo:m{i}" for i in range(5)]

    async def test_semaphore_limits_concurrency(self):
        """信号量限流：并发处理数不超过 MAX_CONCURRENT_AGENTS。"""
        configs = {f"test:s{i}": {"delay": 0.2} for i in range(10)}
        gateway, _ = make_gateway(configs)
        gateway.MAX_CONCURRENT_AGENTS = 3
        gateway._semaphore = asyncio.Semaphore(3)

        running = 0
        peak = 0
        orig_run = FakeAgent.run

        async def tracked_run(self, content, persist_user_message=True):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            try:
                return await orig_run(self, content, persist_user_message)
            finally:
                running -= 1

        FakeAgent.run = tracked_run
        try:
            outbound = await drive(
                gateway, [_msg(f"s{i}", "hi") for i in range(10)], expect=10
            )
        finally:
            FakeAgent.run = orig_run
        assert len(outbound) == 10
        assert peak <= 3


class TestTimeoutAndErrors:
    async def test_task_timeout_friendly_message(self):
        """单条消息超时 → 返回友好提示，不抛异常。"""
        gateway, _ = make_gateway({"test:slow": {"delay": 0.5}})
        gateway.TASK_TIMEOUT = 0.1  # 覆盖超时
        outbound = await drive(gateway, [_msg("slow", "hi")], expect=1)
        assert outbound[0].content == "抱歉，处理超时，请稍后重试或换个问法。"

    async def test_error_isolation(self):
        """一条消息 Agent 崩溃，不影响其他会话，且失败方收到友好错误。"""
        configs = {"test:bad": {"fail": True}, "test:good": {}}
        gateway, _ = make_gateway(configs)
        outbound = await drive(
            gateway, [_msg("bad", "hi"), _msg("good", "hi")], expect=2
        )
        by_chat = {o.chat_id: o.content for o in outbound}
        assert by_chat["chat-bad"] == "抱歉，处理消息时发生了错误。请稍后重试。"
        assert by_chat["chat-good"] == "echo:hi"


class TestHumanTakeover:
    async def test_direct_escalate_no_llm(self):
        """'转人工'直连：不走 LLM，立即建工单并进入接管模式。"""
        gateway, created = make_gateway()
        outbound = await drive(
            gateway, [_msg("u1", "转人工", channel="web")], expect=2
        )

        # 出站：人工接管控制消息 + 工单回复（顺序：控制消息先发）
        assert outbound[0].content == '{"type": "human_takeover", "active": true}'
        assert "工单号" in outbound[1].content
        assert gateway.takeover.is_active("web:u1")

    async def test_takeover_silences_ai(self):
        """接管期间用户消息不触发 AI 回复（无出站消息）。"""
        gateway, _ = make_gateway()
        await drive(gateway, [_msg("u1", "转人工", channel="web")], expect=2)

        proc = asyncio.create_task(gateway._process_inbound())
        await gateway.bus.publish_inbound(_msg("u1", "我的耳机订单在哪", channel="web"))
        await asyncio.sleep(0.5)  # 若有 AI 回复会在此期间入队
        assert gateway.bus.outbound_queue.qsize() == 0
        # 用户消息已写入会话历史（供人工客服查看）
        agent = gateway._agents["web:u1"]
        assert any(
            m.get("content") == "我的耳机订单在哪"
            for _, m in agent.session_manager.saved
        )
        proc.cancel()
        await asyncio.gather(proc, return_exceptions=True)


class TestAgentLRU:
    def test_lru_eviction(self, monkeypatch):
        """超过 MAX_AGENTS 淘汰最久未使用的 Agent。"""
        monkeypatch.setattr(Gateway, "MAX_AGENTS", 3)
        gateway, created = make_gateway()

        for key in ("s1", "s2", "s3"):
            gateway._get_or_create_agent(f"test:{key}")
        assert len(gateway._agents) == 3

        # 再访问 s2（变为最近使用）→ 新增 s4 时应淘汰 s1
        gateway._get_or_create_agent("test:s2")
        gateway._get_or_create_agent("test:s4")
        assert set(gateway._agents.keys()) == {"test:s2", "test:s3", "test:s4"}

        # 被淘汰的 s1 再次访问会重建（会话历史从文件恢复）
        before = len(created)
        gateway._get_or_create_agent("test:s1")
        assert len(created) == before + 1

    def test_lru_move_to_end(self, monkeypatch):
        """命中缓存时移到 LRU 末尾（最近使用）。"""
        monkeypatch.setattr(Gateway, "MAX_AGENTS", 10)
        gateway, _ = make_gateway()
        gateway._get_or_create_agent("test:a")
        gateway._get_or_create_agent("test:b")
        gateway._get_or_create_agent("test:a")  # a 移到末尾
        assert list(gateway._agents.keys()) == ["test:b", "test:a"]


class TestSessionLockCleanup:
    async def test_cleanup_idle_locks(self, monkeypatch):
        """锁缓存超限后清理空闲锁，持锁中的锁不清理。"""
        monkeypatch.setattr(Gateway, "MAX_SESSION_LOCKS", 2)
        gateway, _ = make_gateway()

        lock_a = gateway._get_session_lock("test:a")
        lock_b = gateway._get_session_lock("test:b")
        lock_c = gateway._get_session_lock("test:c")
        assert len(gateway._session_locks) == 3

        async with lock_c:  # c 正在使用
            gateway._cleanup_session_locks()
            assert len(gateway._session_locks) <= 2
            assert "test:c" in gateway._session_locks  # 使用中的保留
        # a/b 中至少一个被清理
        assert "test:a" not in gateway._session_locks or \
               "test:b" not in gateway._session_locks


class TestRunShutdown:
    async def test_channel_start_failure_cleans_up(self):
        """回归：渠道启动失败（如端口冲突）→ run() 清理路径不崩溃，
        shutdown() 正常执行（原 bug：finally 对裸 coroutine 调 .done()
        抛 AttributeError，导致 stop() 永不执行）。"""
        bus = MessageBus()
        stopped = []

        class BrokenChannel(FakeChannel):
            async def start(self):
                raise RuntimeError("port in use")

            async def stop(self):
                stopped.append(self.name)

        gateway = Gateway(bus, [BrokenChannel("broken")], FakeAgent)

        with pytest.raises(RuntimeError):
            await asyncio.wait_for(gateway.run(), timeout=5)

        # shutdown() 被执行，渠道 stop() 被调用（修复前此处根本到不了）
        assert stopped == ["broken"]

    async def test_run_normal_shutdown_stops_channels(self):
        """正常退出路径：gather 完成后同样执行 stop() 清理。"""
        bus = MessageBus()
        stopped = []

        class OnceChannel(FakeChannel):
            def __init__(self, name):
                super().__init__(name)
                self.started = False

            async def start(self):
                self.started = True
                # 立即返回（模拟渠道退出，触发 gather 结束）

            async def stop(self):
                stopped.append(self.name)

        ch = OnceChannel("once")
        gateway = Gateway(bus, [ch], FakeAgent)
        await asyncio.wait_for(gateway.run(), timeout=5)
        assert stopped == ["once"]
