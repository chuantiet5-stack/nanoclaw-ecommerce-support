"""AgentLoop 限流器接线测试（生产落地 P1-2.2 步骤 2）。

用 FakeProvider 验证：
- 每次调用 LLM 前都会经过全局限流器 acquire()
- 限流器接入后 AgentLoop 正常流程不受影响（原有功能回归）
"""

import asyncio

import agent.loop
from agent.context import ContextBuilder
from agent.loop import AgentLoop
from agent.tools.registry import ToolRegistry
from providers.base import LLMResponse
from session.manager import SessionManager


class FakeProvider:
    """离线 LLM 替身：直接返回固定文本。"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, messages, tools=None, model=None):
        self.call_count += 1
        return LLMResponse(content="测试回复", finish_reason="stop")


class CountingLimiter:
    """限流器替身：计数 acquire 调用。"""

    def __init__(self):
        self.acquire_count = 0

    async def acquire(self):
        self.acquire_count += 1


def _build_agent(tmp_path, provider=None):
    session_manager = SessionManager(sessions_dir=str(tmp_path / "sessions"))
    return AgentLoop(
        provider=provider or FakeProvider(),
        tools=ToolRegistry(),
        context=ContextBuilder(workspace=".", identity_file="identity.md"),
        session_manager=session_manager,
        session_key="test:loop",
    )


class TestRateLimiterWiring:
    async def test_acquire_called_per_llm_call(self, tmp_path, monkeypatch):
        """每次 LLM 调用前都会 acquire 一次限流 token。"""
        limiter = CountingLimiter()
        monkeypatch.setattr(agent.loop, "llm_limiter", limiter)
        provider = FakeProvider()
        agent_loop = _build_agent(tmp_path, provider)

        response = await agent_loop.run("你好")

        assert response == "测试回复"
        assert provider.call_count == 1
        assert limiter.acquire_count == 1  # 接线生效

    async def test_real_limiter_does_not_block_normal_use(self, tmp_path):
        """真实限流器（默认 10 QPS / 突发 20）不阻塞正常调用。"""
        agent_loop = _build_agent(tmp_path)

        async def _run():
            return await asyncio.wait_for(agent_loop.run("你好"), timeout=10)

        response = await _run()
        assert response == "测试回复"

    async def test_run_flow_unchanged(self, tmp_path):
        """原有流程回归：用户消息与回复均落盘，返回值正确。"""
        agent_loop = _build_agent(tmp_path)
        response = await agent_loop.run("帮我查订单")

        assert response == "测试回复"
        history = agent_loop.session_manager.get_history("test:loop")
        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant"]
        assert history[0]["content"] == "帮我查订单"
