"""OpenAI 兼容 Provider 重试/超时机制测试（生产落地 P1-2.2 步骤 1）。

使用 mock 模拟 API 行为，不产生真实网络请求：
- 瞬态错误（连接失败/超时/5xx/限流）→ 自动重试
- 确定性错误（鉴权失败）→ 快速失败不重试
- 重试耗尽 → 返回友好错误响应（不抛异常）
"""

from unittest.mock import AsyncMock, patch

import httpx
import openai
import pytest

from providers.base import LLMResponse
from providers.openai_compat import OpenAICompatProvider


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.test/v1/chat/completions")


def _response(status: int = 429) -> httpx.Response:
    return httpx.Response(status_code=status, request=_request())


def _fake_completion(content: str = "好的"):
    """构造最小可用的 chat.completions 响应对象。"""

    class _Msg:
        pass

    class _Choice:
        pass

    class _Usage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2

    class _Resp:
        pass

    _Msg.content = content
    _Msg.tool_calls = None
    _Msg.reasoning_content = None
    _Choice.message = _Msg()
    _Choice.finish_reason = "stop"
    _Resp.choices = [_Choice()]
    _Resp.usage = _Usage()
    return _Resp()


@pytest.fixture
def provider():
    return OpenAICompatProvider(
        api_key="sk-test", base_url="https://api.test/v1", model="test-model"
    )


class TestRetryMechanism:
    async def test_success_no_retry(self, provider):
        """正常响应直接返回，只调用一次。"""
        mock_create = AsyncMock(return_value=_fake_completion("你好呀"))
        with patch.object(provider.client.chat.completions, "create", mock_create):
            resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "你好呀"
        assert resp.finish_reason == "stop"
        assert mock_create.await_count == 1

    async def test_transient_error_retried_then_success(self, provider):
        """连接错误重试后成功（共调用 2 次）。"""
        mock_create = AsyncMock(
            side_effect=[
                openai.APIConnectionError(message="conn reset", request=_request()),
                _fake_completion("恢复了"),
            ]
        )
        with patch.object(provider.client.chat.completions, "create", mock_create):
            resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
        assert resp.finish_reason == "stop"
        assert resp.content == "恢复了"
        assert mock_create.await_count == 2

    async def test_non_retryable_error_fails_fast(self, provider):
        """鉴权失败不重试（仅 1 次调用），返回错误响应。"""
        mock_create = AsyncMock(
            side_effect=openai.AuthenticationError(
                "invalid key", response=_response(401), body=None
            )
        )
        with patch.object(provider.client.chat.completions, "create", mock_create):
            resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
        assert resp.finish_reason == "error"
        assert "API 调用失败" in resp.content
        assert mock_create.await_count == 1  # 未重试

    async def test_retry_exhausted_returns_error(self, provider):
        """限流错误重试耗尽（3 次）后返回友好错误，不抛异常。"""
        err = openai.RateLimitError(
            "rate limited", response=_response(429), body=None
        )
        mock_create = AsyncMock(side_effect=err)
        with patch.object(provider.client.chat.completions, "create", mock_create):
            resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
        assert resp.finish_reason == "error"
        assert "已重试 3 次" in resp.content
        assert mock_create.await_count == 3

    async def test_timeout_error_is_retryable(self, provider):
        """请求超时属于瞬态错误，会重试。"""
        err = openai.APITimeoutError(request=_request())
        mock_create = AsyncMock(
            side_effect=[err, err, _fake_completion("第三次成功")]
        )
        with patch.object(provider.client.chat.completions, "create", mock_create):
            resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
        assert resp.finish_reason == "stop"
        assert mock_create.await_count == 3


class TestClientConfig:
    def test_timeout_configured(self, provider):
        """客户端显式配置了超时（默认 120s，env LLM_REQUEST_TIMEOUT 可调）。"""
        assert provider.REQUEST_TIMEOUT == 120
        assert provider.client.timeout == 120

    def test_interface_unchanged(self, provider):
        """对外接口不变：属性与 chat() 签名保持一致。"""
        assert provider.api_key == "sk-test"
        assert provider.base_url == "https://api.test/v1"
        assert provider.model == "test-model"
        import inspect
        params = list(inspect.signature(provider.chat).parameters)
        assert params == ["messages", "tools", "model"]
