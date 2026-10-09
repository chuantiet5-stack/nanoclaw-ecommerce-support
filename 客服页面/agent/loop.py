"""Agent 主循环模块"""

import json

from providers.base import LLMProvider, LLMResponse
from providers.rate_limiter import llm_limiter
from agent.tools.registry import ToolRegistry
from agent.context import ContextBuilder
from session.manager import SessionManager

class AgentLoop:
    """Agent 执行循环

    管理 LLM 与工具之间的交互循环，包含防爆检测机制。
    """

    # 防爆阈值常量
    FUSE_THRESHOLD = 20  # 熔断阈值
    WARNING_THRESHOLD = 10  # 警告阈值
    WINDOW_SIZE = 30  # 滑动窗口大小

    # 工具名 → 顾客端可见的进度话术（Web 流式体验用）
    TOOL_PROGRESS_TEXT = {
        "search_goods": "🔍 正在搜索在售商品…",
        "get_goods_detail": "📋 正在查询商品实时详情…",
        "query_order": "📦 正在查询您的订单…",
        "query_logistics": "🚚 正在查询物流轨迹…",
        "query_profile": "👤 正在查询顾客档案…",
        "remember_preference": "✍️ 正在记录您的偏好…",
        "submit_refund": "💸 正在提交退款申请…",
        "approve_refund": "🛡️ 正在进行退款审核…",
        "query_refund": "🔍 正在查询退款进度…",
        "create_ticket": "🎫 正在创建工单…",
        "update_address": "📝 正在更新收货地址…",
        "check_refund_policy": "📖 正在查询退款政策…",
        "query_aftersale": "🔧 正在查询售后服务单…",
    }

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        context: ContextBuilder,
        session_manager: SessionManager,
        model: str | None = None,
        max_iterations: int = 32,
        session_key: str = "cli:direct"
    ):
        """初始化 AgentLoop

        Args:
            provider: LLM 提供方实例
            tools: 工具注册表
            context: 上下文构建器
            session_manager: 会话管理器
            model: 模型名称（可选）
            max_iterations: 最大迭代次数
            session_key: 会话标识符，默认 "cli:direct"
        """
        self.provider = provider
        self.tools = tools
        self.context = context
        self.session_manager = session_manager
        self.session_key = session_key
        self.model = model
        self.max_iterations = max_iterations
        self.consolidator = None  # 在整合压缩器之前先设为 None

        # 内部状态
        self._tool_call_history: list[str] = []  # 滑动窗口
        # 从会话管理器恢复历史
        self._session_history: list[dict] = session_manager.get_history(session_key)

    async def run(self, user_message: str, persist_user_message: bool = True,
                  on_event=None) -> str:
        """执行 Agent 主循环

        Args:
            user_message: 用户输入消息
            persist_user_message: 是否将用户消息写入会话历史文件
                （渠道已提前入库时传 False，避免重复保存）
            on_event: 流式事件回调（可选，Web 渠道注入）。事件类型：
                {"type": "tool_start", "text": 进度话术}
                {"type": "text_delta", "text": 流式文本增量}
                {"type": "stream_reset"}   （本轮已推文本作废，前端清空临时气泡）

        Returns:
            str: Agent 的最终响应
        """

        async def _emit(event: dict) -> None:
            if on_event is not None:
                try:
                    await on_event(event)
                except Exception:
                    pass  # 流式事件失败不影响主流程

        # 1. 构建初始 messages
        messages = self.context.build_messages(
            history=self._session_history,
            current_message=user_message
        )

        # 保存用户消息
        if persist_user_message:
            user_msg = {"role": "user", "content": user_message}
            self.session_manager.save_message(self.session_key, user_msg)

        # 2. 循环处理
        for iteration in range(self.max_iterations):
            if hasattr(self, 'consolidator') and self.consolidator:
                messages = await self.consolidator.maybe_consolidate(messages)
            # a. 调用 LLM（打印思考提示）；支持流式的 provider 逐段推送
            print(f"  💭 思考中... (第 {iteration + 1} 轮)", end="", flush=True)
            await llm_limiter.acquire()  # 全局限流：超出配额自动排队，防止打爆 API
            response = None
            stream_pushed = False  # 本轮是否已向前端推送过文本增量
            if on_event is not None and hasattr(self.provider, "chat_stream"):
                async for evt in self.provider.chat_stream(
                    messages=messages,
                    tools=self.tools.get_definitions(),
                    model=self.model,
                ):
                    if evt["type"] == "delta" and evt["text"]:
                        stream_pushed = True
                        await _emit({"type": "text_delta", "text": evt["text"]})
                    elif evt["type"] == "done":
                        response = evt["response"]
            else:
                response = await self.provider.chat(
                    messages=messages,
                    tools=self.tools.get_definitions(),
                    model=self.model
                )
            print(" ✓")  # 思考完成

            # 打印 reasoning_content（思考过程）
            if response.tool_calls and response.tool_calls[0].reasoning_content:
                reasoning = response.tool_calls[0].reasoning_content
                # 截断显示，避免刷屏
                preview = reasoning[:300] + "..." if len(reasoning) > 300 else reasoning
                print(f"  🧠 思考过程:\n{preview}")

            # b. 检查错误
            if response.finish_reason == "error":
                # 终端透出真实 API 错误（如 402 余额不足），便于定位；
                # 顾客侧仍返回友好文案
                print(f"\n  ❌ LLM 调用失败: {(response.content or '未知错误')[:300]}")
                return f"抱歉，发生了错误，请稍后重试。"

            # c. 处理工具调用
            if response.has_tool_calls:
                # 工具轮：已推送的临时文本作废，前端清空
                if stream_pushed:
                    await _emit({"type": "stream_reset"})

                # 构造 assistant 消息（含 tool_calls）
                assistant_msg = self._build_assistant_message(response)
                messages.append(assistant_msg)

                # 保存 assistant 消息（含 tool_calls）
                self.session_manager.save_message(self.session_key, assistant_msg)

                # 遍历每个 tool_call
                for tc in response.tool_calls:
                    # 获取参数 JSON 字符串
                    args_json = json.dumps(tc.arguments, ensure_ascii=False)

                    # ★ 打印工具调用过程，让用户看到 Agent 在干什么
                    print(f"\n  🛠️  调用工具: {tc.name}({args_json})")

                    # 推送工具进度话术（顾客端可见）
                    await _emit({
                        "type": "tool_start",
                        "text": self.TOOL_PROGRESS_TEXT.get(tc.name, "⏳ 正在查询相关信息…"),
                    })

                    # 防爆检测
                    check_result = self._check_tool_loop(tc.name, args_json)

                    if check_result and "熔断" in check_result:
                        # 熔断 - 直接返回
                        print(f"  🚨 {check_result}")
                        return check_result
                    elif check_result and "警告" in check_result:
                        # 警告 - append SYSTEM_ERROR 消息，skip 执行
                        print(f"  ⚠️  {check_result}")
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"系统警告：{check_result}"
                        }
                        messages.append(tool_msg)
                        self.session_manager.save_message(self.session_key, tool_msg)
                    else:
                        # 防爆通过 - 执行工具
                        result = await self.tools.execute(tc.name, tc.arguments)
                        # 打印工具结果（截断显示，避免刷屏）
                        preview = result[:200] + "..." if len(result) > 200 else result
                        print(f"  ✅ 结果: {preview}")
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result
                        }
                        messages.append(tool_msg)
                        self.session_manager.save_message(self.session_key, tool_msg)

                # continue 回到循环顶部
                continue

            # d. 没有工具调用 - 保存历史并返回
            self._save_to_history(messages)

            # 保存最终的 assistant 消息（不含 tool_calls）
            if response.content:
                assistant_final_msg = {"role": "assistant", "content": response.content}
                self.session_manager.save_message(self.session_key, assistant_final_msg)

            return response.content or ""

        # 3. for-else: 超过最大次数
        return "已达到最大迭代次数，任务未完成。"

    def _build_assistant_message(self, response: LLMResponse) -> dict:
        """构建 assistant 消息（含 tool_calls）

        严格按照 OpenAI 格式构建，reasoning_content 放在顶层。

        Args:
            response: LLM 响应

        Returns:
            dict: assistant 消息字典
        """
        assistant_msg: dict = {
            "role": "assistant",
            "content": response.content
        }

        # 构建 tool_calls 数组
        tool_calls = []
        for tc in response.tool_calls:
            tool_call_item = {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                }
            }
            tool_calls.append(tool_call_item)

        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls

        # reasoning_content 放在顶层（不能放在 tool_calls 里）
        if response.tool_calls and response.tool_calls[0].reasoning_content:
            assistant_msg["reasoning_content"] = response.tool_calls[0].reasoning_content

        return assistant_msg

    def _check_tool_loop(self, tool_name: str, tool_args_json: str) -> str | None:
        """防爆检测

        检测工具调用的重复频率，防止无限循环。

        Args:
            tool_name: 工具名称
            tool_args_json: 工具参数的 JSON 字符串

        Returns:
            str | None: 熔断/警告消息，或 None 表示放行
        """
        signature = f"{tool_name}:{tool_args_json}"

        # 统计签名出现次数
        count = self._tool_call_history.count(signature)

        if count >= self.FUSE_THRESHOLD:
            return f"检测到工具 '{tool_name}' 重复调用已达熔断阈值（{self.FUSE_THRESHOLD}次），已自动终止执行。请尝试其他方案。"
        elif count >= self.WARNING_THRESHOLD:
            return f"警告：工具 '{tool_name}' 重复调用已达 {self.WARNING_THRESHOLD} 次，请检查是否有循环调用问题。"

        # 放行 - 添加到历史
        self._tool_call_history.append(signature)

        # 维护滑动窗口大小
        if len(self._tool_call_history) > self.WINDOW_SIZE:
            self._tool_call_history.pop(0)

        return None

    def _save_to_history(self, messages_snapshot: list[dict]) -> None:
        """保存本轮新增消息到会话历史

        注意：消息已通过 session_manager.save_message 持久化，
        此方法仅更新内存中的 _session_history，供下次对话使用。

        Args:
            messages_snapshot: 当前完整的消息列表
        """
        # 计算新增消息（跳过 system 消息）
        # 新消息数量 = 总消息数 - 已有历史消息数 - 1 (system)
        existing_count = len(self._session_history)

        # 新增的消息（不包含 system）
        new_messages = messages_snapshot[1 + existing_count:]

        # 添加到历史（仅更新内存状态）
        self._session_history.extend(new_messages)

    def clear_history(self) -> None:
        """清空对话历史和工具调用历史"""
        self._tool_call_history.clear()
        self._session_history.clear()
        # 清空会话文件
        self.session_manager.clear(self.session_key)