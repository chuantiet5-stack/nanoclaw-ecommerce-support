"""
OpenAI 兼容 Provider 实现

兼容 OpenAI API 格式的各种服务商，包括：
- OpenAI 官方 API
- 硅基流动 (SiliconFlow)
- Kimi (Moonshot)
- DeepSeek
- 其他 OpenAI 兼容的第三方服务

通过统一接口封装，自动处理工具调用参数、推理内容提取和异常容错。
"""

import json
import os
from typing import Any

from openai import (APIConnectionError, APITimeoutError, AsyncOpenAI,
                    InternalServerError, RateLimitError)
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from providers.base import LLMProvider, LLMResponse, ToolCallRequest


class OpenAICompatProvider(LLMProvider):
    """
    OpenAI 兼容 API Provider

    支持所有 OpenAI API 格式的服务，通过 base_url 切换不同提供商。
    内置异常容错，API 调用失败时返回错误响应而非抛出异常。

    关键特性：
    - 仅在 tools 参数存在时传递 tool_choice，避免部分 API 报错
    - 自动提取 reasoning_content（支持 Kimi-K2.5、DeepSeek-R1 等思考模型）
    - 自动解析 tool_calls 的 arguments JSON

    使用示例：
        # OpenAI 官方
        provider = OpenAICompatProvider(
            api_key="sk-xxx",
            base_url="https://api.openai.com/v1",
            model="gpt-4"
        )

        # 硅基流动
        provider = OpenAICompatProvider(
            api_key="sk-xxx",
            base_url="https://api.siliconflow.cn/v1",
            model="Qwen/Qwen2.5-72B-Instruct"
        )

        # DeepSeek
        provider = OpenAICompatProvider(
            api_key="sk-xxx",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat"
        )
    """

    # 仅对这些"瞬态错误"自动重试（网络抖动/超时/服务端 5xx/限流）；
    # 鉴权失败、参数错误等确定性失败不重试，避免浪费时间
    RETRYABLE_ERRORS = (APITimeoutError, APIConnectionError,
                        InternalServerError, RateLimitError)
    # 请求超时（秒）。默认 120 而非 30：思考型模型的长响应可能超过 30s，
    # 过短会切断正常回复；可通过环境变量 LLM_REQUEST_TIMEOUT 调整
    REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
    MAX_RETRIES = 3
    # GLM 思考强度：GLM-5.3 为强制思考模型（thinking 无法关闭），但支持
    # reasoning_effort=low/high/max 控制推理深度。默认 low 可显著提速
    # （实测首字延迟约降为 1/6），客服简单查询无需深度推理，回答质量基本不变。
    # 可通过环境变量 LLM_REASONING_EFFORT 调整；设为空串则不注入该参数。
    REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        """
        Args:
            api_key: API 密钥
            base_url: API 基础 URL，不同服务商地址不同
            model: 默认使用的模型名称
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.reasoning_effort = self.REASONING_EFFORT
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.REQUEST_TIMEOUT,  # 单次请求超时
            max_retries=0,  # SDK 内置重试关闭，由 tenacity 统一控制
        )

    def _apply_reasoning_effort(self, request_params: dict[str, Any]) -> dict[str, Any]:
        """注入 GLM 思考强度参数（OpenAI SDK 非标准参数走 extra_body 透传）。"""
        if self.reasoning_effort:
            request_params["extra_body"] = {"reasoning_effort": self.reasoning_effort}
        return request_params

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),  # 2s, 4s, 8s
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        reraise=True,
    )
    async def _chat_with_retry(self, request_params: dict[str, Any]):
        """带重试的 API 调用（仅瞬态错误重试，指数退避）。"""
        return await self.client.chat.completions.create(**request_params)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """
        发送对话请求

        动态构建请求参数：仅在 tools 存在时传递 tool_choice，
        避免硅基流动等 API 报 "tools must be specified when tool_choice is utilized" 错误。
        """
        try:
            # 动态构建请求参数
            request_params: dict[str, Any] = {
                "model": model or self.model,
                "messages": messages,
            }

            # 仅在 tools 存在时添加工具相关参数
            if tools:
                request_params["tools"] = tools
                request_params["tool_choice"] = "auto"

            # 注入 GLM 思考强度（reasoning_effort=low 提速）
            self._apply_reasoning_effort(request_params)

            # 调用 API（带超时 + 瞬态错误自动重试）
            response = await self._chat_with_retry(request_params)

            # 解析响应
            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason or "stop"

            # 提取 usage
            usage: dict[str, Any] = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            # 获取 message 顶层的 reasoning_content（部分模型放在这里）
            message_reasoning = getattr(message, "reasoning_content", None)

            # 解析 tool_calls
            tool_calls: list[ToolCallRequest] = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    # 解析 arguments JSON
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {"raw": tc.function.arguments}

                    # 获取 tool_call 级别的 reasoning_content
                    tc_reasoning = getattr(tc, "reasoning_content", None)

                    # 合并 reasoning：优先 tool_call 级别，其次 message 级别
                    reasoning_content = tc_reasoning or message_reasoning

                    tool_calls.append(
                        ToolCallRequest(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=arguments,
                            reasoning_content=reasoning_content,
                        )
                    )

            return LLMResponse(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
            )

        except Exception as e:
            # 异常容错：返回错误响应而非抛出异常
            if isinstance(e, self.RETRYABLE_ERRORS):
                error_text = (
                    f"错误: API 调用失败（已重试 {self.MAX_RETRIES} 次）"
                    f"- {type(e).__name__}"
                )
            else:
                error_text = f"错误: API 调用失败 - {e}"
            return LLMResponse(
                content=error_text,
                finish_reason="error",
            )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ):
        """流式对话：逐段产出文本增量，最终产出完整 LLMResponse。

        yield 事件序列：
            {"type": "delta", "text": "…"}   文本增量（可多次）
            {"type": "done", "response": LLMResponse}   最终完整响应

        流式失败（网络/服务商不支持）自动降级为非流式 chat()，
        行为与非流式完全一致，调用方无需感知。
        """
        request_params: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = "auto"

        # 注入 GLM 思考强度（reasoning_effort=low 提速）
        self._apply_reasoning_effort(request_params)

        try:
            stream = await self.client.chat.completions.create(**request_params)
        except Exception:
            # 建流失败 → 降级非流式（复用其重试与容错）
            response = await self.chat(messages, tools, model)
            yield {"type": "done", "response": response}
            return

        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}  # index → {id, name, arguments}
        finish_reason = "stop"
        message_reasoning: str | None = None

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                if delta is None:
                    continue

                # 思考模型的推理内容（不推给用户，仅保留在响应里）
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    message_reasoning = (message_reasoning or "") + rc

                # 文本增量
                if delta.content:
                    content_parts.append(delta.content)
                    yield {"type": "delta", "text": delta.content}

                # 工具调用增量（按 index 累积拼接）
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] += tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        except Exception as e:
            # 流中断：已收到的内容可用则用，否则降级
            if not content_parts and not tool_acc:
                response = await self.chat(messages, tools, model)
                yield {"type": "done", "response": response}
                return
            finish_reason = "error" if not content_parts else finish_reason

        # 组装完整响应（结构与非流式 chat() 一致）
        tool_calls: list[ToolCallRequest] = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            try:
                arguments = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {"raw": slot["arguments"]}
            tool_calls.append(
                ToolCallRequest(
                    id=slot["id"],
                    name=slot["name"],
                    arguments=arguments,
                    reasoning_content=message_reasoning,
                )
            )

        yield {
            "type": "done",
            "response": LLMResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            ),
        }