"""
记忆压缩器模块

MemoryConsolidator 负责压缩对话历史，防止 Token 超限：
- 估算消息列表的 Token 数量
- 当超过预算时，保留关键消息，压缩旧消息为摘要
- 摘要追加写入 HISTORY.md 文件，记录压缩历史

使用示例：
    consolidator = MemoryConsolidator(
        provider=llm_provider,
        workspace="/path/to/project",
        token_budget=6000
    )

    # 在添加新消息前检查是否需要压缩
    compressed_messages = await consolidator.maybe_consolidate(messages)
"""

import json
import os
from datetime import datetime
from typing import Any

from providers.base import LLMProvider


class MemoryConsolidator:
    """
    记忆压缩器

    当对话历史过长时，自动压缩旧消息为摘要，保留关键信息。
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: str,
        token_budget: int = 6000,
    ) -> None:
        """
        Args:
            provider: LLM 提供方实例，用于生成摘要
            workspace: 工作区路径，用于写入 HISTORY.md
            token_budget: Token 预算，超过时触发压缩
        """
        self.provider = provider
        self.workspace = workspace
        self.token_budget = token_budget

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """
        估算消息列表的 Token 数量

        遍历所有消息，累加 len(json.dumps(msg, ensure_ascii=False)) // 2。
        这是一个粗略估算，假设平均每个 Token 约占 2 个字符。

        Args:
            messages: 消息列表

        Returns:
            int: 估算的 Token 数量
        """
        total_chars = 0

        for msg in messages:
            # 序列化为 JSON 字符串（支持中文）
            msg_json = json.dumps(msg, ensure_ascii=False)
            total_chars += len(msg_json)

        # 假设平均每个 Token 约占 2 个字符
        return total_chars // 2

    async def maybe_consolidate(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        检查并压缩对话历史

        如果 estimate_tokens(messages) <= token_budget，直接返回原 messages。
        否则：
        a. 保留第一条（system prompt）和最后 6 条消息不动
        b. 取中间的旧消息（要被压缩的部分）
        c. 调用 _summarize(old_messages) 生成摘要
        d. 用一条 {"role": "system", "content": "[历史摘要]: {摘要内容}"} 替换旧消息
        e. 把摘要追加写入 workspace/memory/HISTORY.md（带时间戳）
        f. 返回压缩后的 messages 列表

        Args:
            messages: 当前消息列表

        Returns:
            list[dict]: 压缩后的消息列表（或原列表）
        """
        # 估算 Token 数量
        estimated_tokens = self.estimate_tokens(messages)

        # 如果未超预算，直接返回
        if estimated_tokens <= self.token_budget:
            return messages
        
        print(f"\n  🗜️ Token 预算超出（{estimated_tokens}/{self.token_budget}），正在压缩...")

        # 检查消息数量是否足够压缩
        # 至少需要：第一条 + 最后 6 条 + 至少 1 条中间消息 = 8 条
        if len(messages) < 8:
            print(f"警告: Token 数量 {estimated_tokens} 超过预算 {self.token_budget}，但消息数量不足，无法压缩")
            return messages

        # 保留第一条（system prompt）和最后 6 条
        first_msg = messages[0]
        last_messages = messages[-6:]

        # 取中间的旧消息（要被压缩的部分）
        old_messages = messages[1:-6]

        print(f"压缩对话历史: Token {estimated_tokens} > {self.token_budget}, 压缩 {len(old_messages)} 条旧消息...")

        # 调用 _summarize 生成摘要
        summary = await self._summarize(old_messages)

        # 用一条系统消息替换旧消息
        summary_msg = {
            "role": "system",
            "content": f"[历史摘要]: {summary}",
        }

        # 构造压缩后的消息列表
        compressed_messages = [first_msg, summary_msg] + last_messages

        # 写入 HISTORY.md
        self._save_to_history(summary, len(old_messages))

        print(f"压缩完成: Token 从 {estimated_tokens} 降至约 {self.estimate_tokens(compressed_messages)}")

        return compressed_messages

    async def _summarize(self, messages: list[dict[str, Any]]) -> str:
        """
        生成旧消息的摘要

        把旧消息拼接成文本，构造摘要请求发给 provider.chat()。
        如果调用失败，返回默认文本。

        Args:
            messages: 要压缩的旧消息列表

        Returns:
            str: 摘要文本
        """
        # 拼接旧消息为文本
        conversation_text = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # 跳过工具调用消息（太长且不重要）
            if msg.get("tool_calls") or msg.get("tool_call_id"):
                continue

            conversation_text.append(f"{role}: {content}")

        conversation_str = "\n".join(conversation_text)

        # 构造摘要请求
        summary_request = [
            {
                "role": "user",
                "content": f"""请用 3-5 句话概括以下对话的关键信息，保留重要的事实和结论，省略过程细节和寒暄。只输出摘要，不要其他内容。

对话内容：
{conversation_str}"""
            }
        ]

        # 调用 LLM 生成摘要
        try:
            response = await self.provider.chat(
                messages=summary_request,
                tools=None,  # 不使用工具
            )

            # 提取摘要内容
            if response.content:
                return response.content.strip()
            else:
                return "（摘要生成失败，旧消息已丢弃）"

        except Exception as e:
            print(f"警告: 摘要生成失败 - {e}")
            return "（摘要生成失败，旧消息已丢弃）"

    def _save_to_history(self, summary: str, original_count: int) -> None:
        """
        追加写入压缩历史到 HISTORY.md

        格式："## {当前时间}\n压缩了 {original_count} 条旧消息\n\n{summary}\n\n---\n"

        Args:
            summary: 摘要内容
            original_count: 压缩的旧消息数量
        """
        # 构建文件路径
        history_dir = os.path.join(self.workspace, "workspace", "memory")
        history_file = os.path.join(history_dir, "HISTORY.md")

        # 确保目录存在
        os.makedirs(history_dir, exist_ok=True)

        # 构建写入内容
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = f"""## {current_time}
压缩了 {original_count} 条旧消息

{summary}

---

"""

        # 追加写入
        try:
            with open(history_file, "a", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"警告: 写入 HISTORY.md 失败 - {e}")