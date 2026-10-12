"""
Gateway 网关模块

Gateway 是多渠道接入的核心网关，负责：
- 管理多个渠道适配器（Channel）
- 处理入站消息：从 MessageBus 消费 → 分发给对应的 Agent
- 分发出站消息：从 MessageBus 消费 → 发送给对应的渠道
- 缓存 Agent 实例（按 session_key）

使用示例：
    bus = MessageBus()
    cli_channel = CLIChannel(bus)

    def create_agent(session_key: str) -> AgentLoop:
        # 根据 session_key 创建 Agent
        ...

    gateway = Gateway(
        bus=bus,
        channels=[cli_channel],
        agent_factory=create_agent
    )

    # 启动网关
    asyncio.run(gateway.run())
"""

import asyncio
import json
import os
import re
import time
from collections import OrderedDict
from typing import Callable

from bus.queue import MessageBus, InboundMessage, OutboundMessage
from channels.base import Channel
from agent.loop import AgentLoop
from agent.tools.ecom.store import resolve_channel_actor

# 触发"直接转人工"的关键词（命中即跳过 LLM 思考，立即创建人工工单并进入人工接管模式）
HUMAN_TRANSFER_RE = re.compile(r"转人工|人工客服|找真人|真人客服|投诉到底|我要投诉")


class HumanTakeoverManager:
    """人工接管状态管理器。

    记录哪些会话当前处于"人工客服接管"状态：
    - 接管期间用户消息不再交给 AI 回答，仅写入会话历史供人工客服查看；
    - 用户/人工客服任意一方有新对话都会刷新活跃时间；
    - 超过超时时间（默认 300 秒）双方均无对话，自动转回 AI。
    """

    def __init__(self) -> None:
        # session_key -> {"channel": str, "chat_id": str, "last_activity": float}
        self._sessions: dict[str, dict] = {}
        # session_key -> AI 为人工客服生成的最新推荐话术（仅客服端可见，不发给顾客）
        self.suggestions: dict[str, str] = {}

    def activate(self, session_key: str, channel: str, chat_id: str) -> None:
        """会话进入人工接管模式。"""
        self._sessions[session_key] = {
            "channel": channel,
            "chat_id": chat_id,
            "last_activity": time.monotonic(),
        }

    def deactivate(self, session_key: str) -> None:
        """会话退出人工接管模式。"""
        self._sessions.pop(session_key, None)

    def is_active(self, session_key: str) -> bool:
        return session_key in self._sessions

    def touch(self, session_key: str, chat_id: str | None = None) -> None:
        """刷新会话活跃时间（用户或人工客服有新对话时调用）。

        chat_id 用于用户重连后更新推送目标。
        """
        rec = self._sessions.get(session_key)
        if rec is None:
            return
        rec["last_activity"] = time.monotonic()
        if chat_id:
            rec["chat_id"] = chat_id

    def expired(self, timeout: float) -> list[tuple[str, str, str]]:
        """返回超时（双方无对话超过 timeout 秒）的会话列表 [(session_key, channel, chat_id)]。"""
        now = time.monotonic()
        return [
            (key, rec["channel"], rec["chat_id"])
            for key, rec in self._sessions.items()
            if now - rec["last_activity"] >= timeout
        ]

    def active_sessions(self) -> list[dict]:
        """返回当前处于人工接管模式的会话列表（客服端提醒/高亮用）。"""
        now = time.monotonic()
        return [
            {
                "session_key": key,
                "channel": rec["channel"],
                "chat_id": rec["chat_id"],
                "idle_seconds": int(now - rec["last_activity"]),
            }
            for key, rec in self._sessions.items()
        ]


class Gateway:
    """
    网关核心类

    管理多渠道接入和消息路由。

    并发架构（生产落地 P0-1.3）：
    - 入站消费循环只负责快速取消息，每条消息创建独立 Task 并发处理；
    - 同一会话内通过会话锁严格串行（保持消息顺序与原有语义）；
    - 跨会话并发受信号量限流（MAX_CONCURRENT_AGENTS）；
    - 单条消息处理有超时保护（TASK_TIMEOUT），超时返回友好提示。

    Agent 缓存（生产落地 P1-2.1）：
    - 使用 LRU（OrderedDict）缓存 Agent 实例，超过 MAX_AGENTS 淘汰最久未使用的，
      防止长期运行内存泄漏。
    """

    # 并发控制参数（生产落地 P0-1.3）
    MAX_CONCURRENT_AGENTS = 20   # 最大并发 Agent 处理数（跨会话）
    # 单条消息处理超时（秒）。默认 300 而非 120：复杂任务（多轮工具调用）
    # 可能超过 2 分钟，过短会切断正常处理；可通过环境变量 AGENT_TASK_TIMEOUT 调整
    TASK_TIMEOUT = int(os.getenv("AGENT_TASK_TIMEOUT", "300"))

    # Agent 实例 LRU 缓存上限（生产落地 P1-2.1）
    MAX_AGENTS = 500
    # 会话锁缓存上限（超限后清理空闲锁，防泄漏）
    MAX_SESSION_LOCKS = 1000

    def __init__(
        self,
        bus: MessageBus,
        channels: list[Channel],
        agent_factory: Callable[[str], AgentLoop],
        human_transfer_timeout: float = 300,
    ) -> None:
        """
        Args:
            bus: 消息总线实例
            channels: 渠道适配器列表
            agent_factory: Agent 创建函数，输入 session_key，返回 AgentLoop 实例
            human_transfer_timeout: 人工接管后双方无对话多久自动转回 AI（秒）
        """
        self.bus = bus
        self.channels = channels
        self.agent_factory = agent_factory
        self.human_transfer_timeout = human_transfer_timeout

        # 内部状态
        self._agents: OrderedDict[str, AgentLoop] = OrderedDict()  # 按 session_key LRU 缓存
        self._channel_map: dict[str, Channel] = {}  # 按渠道名索引渠道
        self.takeover = HumanTakeoverManager()   # 人工接管状态
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_AGENTS)  # 跨会话并发限流
        self._active_tasks: set[asyncio.Task] = set()  # 进行中的消息处理任务
        self._session_locks: dict[str, asyncio.Lock] = {}  # 每会话串行锁

        # 构建渠道映射表
        for channel in channels:
            self._channel_map[channel.name] = channel

    async def run(self) -> None:
        """
        启动网关

        用 asyncio.gather 并发启动：
        - 所有渠道的 start()
        - _process_inbound() 入站消费循环
        - _dispatch_outbound() 出站分发循环
        """
        # 构建任务列表
        tasks = []

        # 添加所有渠道的启动任务
        for channel in self.channels:
            tasks.append(channel.start())

        # 添加入站和出站处理循环
        tasks.append(self._process_inbound())
        tasks.append(self._dispatch_outbound())
        tasks.append(self._takeover_timeout_loop())

        # 将 coroutine 统一包装为 Task（生产落地修复）：
        # finally 中 task.done()/task.cancel() 需要 Task 对象，裸 coroutine
        # 会触发 AttributeError（原有潜在缺陷，端口冲突导致启动失败时暴露，
        # 致使清理路径崩溃、shutdown() 不执行）
        tasks = [asyncio.ensure_future(t) for t in tasks]

        # 并发启动所有任务
        try:
            # 等待所有任务（通常会阻塞直到某个渠道退出）
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            # 取消所有未完成的任务
            for task in tasks:
                if not task.done():
                    task.cancel()
            # 等待任务取消完成（忽略 CancelledError）
            await asyncio.gather(*tasks, return_exceptions=True)
            # 关闭所有渠道
            await self.shutdown()

    async def _process_inbound(self) -> None:
        """
        处理入站消息循环（生产落地 P0-1.3：并发消费）

        while True 循环：
        - 从 bus.consume_inbound() 快速取出消息
        - 为每条消息创建独立 Task 并发处理（_handle_message）
          同一会话内通过会话锁保持严格串行（消息顺序与原串行架构一致），
          不同会话之间并发执行，互不阻塞
        """
        while True:
            try:
                # 从消息总线消费入站消息
                inbound_msg = await self.bus.consume_inbound()

                # 创建独立 Task，不阻塞下一条消息消费
                task = asyncio.create_task(self._handle_message(inbound_msg))
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)

            except asyncio.CancelledError:
                # 任务被取消：取消所有进行中的处理任务后退出循环
                for t in self._active_tasks:
                    t.cancel()
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
                break

            except Exception as e:
                # 消费循环异常，打印日志并继续
                print(f"警告: 入站处理循环异常 - {e}")
                continue

    def _resolve_session_key(self, inbound_msg: InboundMessage) -> str:
        """解析入站消息对应的会话键（跨渠道共用会话）。

        QQ / 飞书渠道免登录（绑定表或默认演示账号）解析出登录身份时，
        复用 Web 端同账号的会话键 web:{username}，使同一人在 Web、QQ、飞书
        三端共用同一份对话历史（web_{username}.jsonl）与偏好记忆；
        其余情况（Web 游客 / CLI / 未识别出身份的渠道用户）保持原有
        {channel}:{sender_id}，行为不变。

        注意：仅统一会话键，消息回复仍按 inbound_msg 的 channel/chat_id
        发回原渠道，渠道路由不受影响。
        """
        channel = inbound_msg.channel
        if channel in ("qq", "feishu"):
            actor, logged_in = resolve_channel_actor(channel, inbound_msg.sender_id)
            if logged_in:
                return f"web:{actor}"
        return f"{channel}:{inbound_msg.sender_id}"

    async def _handle_message(self, inbound_msg: InboundMessage) -> None:
        """
        处理单条消息（会话串行 + 并发限流 + 超时保护 + 错误隔离）

        - 会话锁：同一 session_key 的消息严格按序处理（保持原有串行语义）
        - 信号量：跨会话并发上限 MAX_CONCURRENT_AGENTS
        - 超时：agent.run 超过 TASK_TIMEOUT 返回友好提示
        - 错误隔离：单条消息异常不影响其他消息
        """
        session_key = self._resolve_session_key(inbound_msg)
        lock = self._get_session_lock(session_key)

        async with lock:  # 同一会话内串行
            async with self._semaphore:  # 跨会话并发限流
                try:
                    # 从 LRU 缓存获取 Agent，不存在则创建
                    agent = self._get_or_create_agent(session_key)

                    # ---- 人工接管期间：AI 不参与回答，但在后台为客服生成推荐话术 ----
                    # 用户消息仅写入会话历史（供人工客服在客服端查看），
                    # 并刷新活跃时间，不调用 Agent；AI 推荐话术仅存入接管管理器，
                    # 由客服端轮询获取，绝不发送给顾客
                    if self.takeover.is_active(session_key):
                        if not inbound_msg.persisted:
                            agent.session_manager.save_message(
                                session_key,
                                {"role": "user", "content": inbound_msg.content},
                            )
                        self.takeover.touch(session_key, chat_id=inbound_msg.chat_id)
                        asyncio.create_task(self._generate_suggestion(session_key, agent))
                        return

                    # ---- "转人工"直连：跳过 LLM 思考，立即创建人工工单 ----
                    if HUMAN_TRANSFER_RE.search(inbound_msg.content):
                        if not inbound_msg.persisted:
                            agent.session_manager.save_message(
                                session_key, {"role": "user", "content": inbound_msg.content}
                            )
                        response = await self._direct_escalate(agent, inbound_msg.content)
                        agent.session_manager.save_message(
                            session_key, {"role": "assistant", "content": response}
                        )
                        if "工单号" in response:
                            self.takeover.activate(
                                session_key, inbound_msg.channel, inbound_msg.chat_id
                            )
                            print(f"[Gateway] 会话 {session_key} 已进入人工接管模式（AI 暂停回复）")
                            # 通知顾客端进入人工模式（隐藏 AI 思考动画；仅 Web 渠道支持控制消息）
                            if inbound_msg.channel == "web":
                                await self.bus.publish_outbound(OutboundMessage(
                                    channel=inbound_msg.channel,
                                    chat_id=inbound_msg.chat_id,
                                    content=json.dumps(
                                        {"type": "human_takeover", "active": True},
                                        ensure_ascii=False,
                                    ),
                                ))
                            # 转人工后立即为客服生成首条推荐话术
                            asyncio.create_task(self._generate_suggestion(session_key, agent))
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=inbound_msg.channel,
                            chat_id=inbound_msg.chat_id,
                            content=response,
                        ))
                        return

                    # 调用 Agent 处理消息（persisted=True 时 Agent 循环不再重复保存用户消息）
                    try:
                        # Web 渠道注入流式回调：工具进度 / 文本增量实时推送给前端
                        stream_state: dict = {"pushed": False} \
                            if inbound_msg.channel == "web" else None
                        on_event = self._make_stream_emitter(inbound_msg, stream_state) \
                            if stream_state is not None else None

                        response = await asyncio.wait_for(
                            agent.run(
                                inbound_msg.content,
                                persist_user_message=not inbound_msg.persisted,
                                on_event=on_event,
                            ),
                            timeout=self.TASK_TIMEOUT,
                        )

                        # 流式推送过增量 → 发完成标记固化气泡；
                        # 从未推送（流式降级/纯工具异常）→ 正常发全文
                        if stream_state is not None and stream_state.get("pushed"):
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=inbound_msg.channel,
                                chat_id=inbound_msg.chat_id,
                                content=json.dumps(
                                    {"type": "agent_done"}, ensure_ascii=False
                                ),
                            ))
                        else:
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=inbound_msg.channel,
                                chat_id=inbound_msg.chat_id,
                                content=response,
                            ))

                        # ---- 售后转人工（退款金额超限等）：提醒后台人工 ----
                        # approve_refund 转人工分支的回复含「已提交人工审核」：
                        # 据此进入人工接管模式，客服端轮询 /api/takeovers
                        # 触发弹窗+声音+红色闪烁标签，后台人工及时跟进
                        if "已提交人工审核" in response:
                            self.takeover.activate(
                                session_key, inbound_msg.channel, inbound_msg.chat_id
                            )
                            print(
                                f"[Gateway] 会话 {session_key} 售后转人工"
                                "（已创建工单），进入人工接管模式并提醒后台人工"
                            )
                            # 通知顾客端进入人工模式（仅 Web 渠道支持控制消息）
                            if inbound_msg.channel == "web":
                                await self.bus.publish_outbound(OutboundMessage(
                                    channel=inbound_msg.channel,
                                    chat_id=inbound_msg.chat_id,
                                    content=json.dumps(
                                        {"type": "human_takeover", "active": True},
                                        ensure_ascii=False,
                                    ),
                                ))
                            # 为人工客服生成首条推荐话术
                            asyncio.create_task(
                                self._generate_suggestion(session_key, agent)
                            )

                    except asyncio.TimeoutError:
                        # 单条消息处理超时，返回友好提示（不拖垮整个会话）
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=inbound_msg.channel,
                            chat_id=inbound_msg.chat_id,
                            content="抱歉，处理超时，请稍后重试或换个问法。",
                        ))
                        print(f"警告: 会话 {session_key} 处理超时（{self.TASK_TIMEOUT}s）")

                    except Exception as e:
                        # Agent 出错，发送友好错误消息（错误隔离，不影响其他会话）
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=inbound_msg.channel,
                            chat_id=inbound_msg.chat_id,
                            content="抱歉，处理消息时发生了错误。请稍后重试。",
                        ))
                        # 打印错误日志（调试用）
                        print(f"警告: Agent 处理出错 - {e}")

                except Exception as e:
                    # 外层兜底（如 Agent 创建失败），保证任何异常都有回复
                    try:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=inbound_msg.channel,
                            chat_id=inbound_msg.chat_id,
                            content="抱歉，处理消息时发生了错误。请稍后重试。",
                        ))
                    except Exception:
                        pass
                    print(f"警告: 消息处理异常 [{session_key}] - {e}")

        # 清理空闲会话锁（防长期运行泄漏；仅在持锁期间无并发时安全）
        self._cleanup_session_locks()

    def _make_stream_emitter(self, inbound_msg: InboundMessage, state: dict):
        """构造 Web 渠道流式事件回调。

        把 Agent 循环产生的流式事件（工具进度/文本增量）包装成
        JSON 控制消息经 bus 发出，由前端拼接展示。
        state["pushed"] 记录是否推送过文本增量（用于降级兜底）。

        控制消息协议（复用 Web 渠道现有 JSON 控制消息机制）：
            {"type": "agent_tool", "text": "🔍 正在搜索在售商品…"}
            {"type": "agent_delta", "text": "流式文本增量"}
            {"type": "agent_reset"}
        """
        channel = inbound_msg.channel
        chat_id = inbound_msg.chat_id

        async def _emit(event: dict) -> None:
            etype = event.get("type")
            payload = None
            if etype == "tool_start":
                payload = {"type": "agent_tool", "text": event.get("text", "")}
            elif etype == "text_delta":
                state["pushed"] = True
                payload = {"type": "agent_delta", "text": event.get("text", "")}
            elif etype == "stream_reset":
                state["pushed"] = False
                payload = {"type": "agent_reset"}
            if payload is not None:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=json.dumps(payload, ensure_ascii=False),
                ))

        return _emit

    def _get_or_create_agent(self, session_key: str) -> AgentLoop:
        """获取或创建 Agent（LRU 缓存，生产落地 P1-2.1）。

        - 命中缓存：移到 LRU 末尾（最近使用）
        - 缓存超上限：淘汰最久未使用的 Agent（其会话历史已持久化在文件中，
          下次消息会自动从文件恢复）
        """
        if session_key in self._agents:
            self._agents.move_to_end(session_key)
            return self._agents[session_key]

        if len(self._agents) >= self.MAX_AGENTS:
            oldest_key, _oldest = self._agents.popitem(last=False)
            print(f"[LRU] 淘汰空闲 Agent: {oldest_key}")

        agent = self.agent_factory(session_key)
        self._agents[session_key] = agent
        return agent

    def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """获取会话串行锁（不存在则创建）。"""
        lock = self._session_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_key] = lock
        return lock

    def _cleanup_session_locks(self) -> None:
        """清理空闲会话锁，防止锁字典无限增长。

        仅删除未上锁的锁：get-or-create 与 acquire 之间无 await，
        因此"已取出引用但尚未上锁"的窗口不存在，清理是安全的。
        """
        if len(self._session_locks) <= self.MAX_SESSION_LOCKS:
            return
        excess = len(self._session_locks) - self.MAX_SESSION_LOCKS
        idle_keys = [k for k, v in self._session_locks.items() if not v.locked()]
        for k in idle_keys[:excess]:
            del self._session_locks[k]

    async def _direct_escalate(self, agent: AgentLoop, user_message: str) -> str:
        """不经过 LLM，直接调用转人工工具创建工单。"""
        text = user_message.strip()
        summary = text[:100] if len(text) > 8 else "顾客明确要求转人工服务"
        result = await agent.tools.execute("escalate_to_human", {
            "summary": summary,
            "reason": "顾客明确要求转人工",
        })
        if "工单号" in result:
            result += "\n\n已切换为人工服务模式：您接下来的消息将由人工客服处理，AI 暂停回复。"
        return result

    async def _generate_suggestion(self, session_key: str, agent: AgentLoop) -> None:
        """为人工客服生成推荐话术（后台执行，仅客服端可见，不发送给顾客）。

        基于会话最近上下文 + 顾客最新消息，让 LLM 生成一条可直接使用的回复。
        仅在线模式（Agent 有 provider）生效；离线模式由客服端关键词规则兜底。
        """
        provider = getattr(agent, "provider", None)
        if provider is None:
            return
        try:
            # 从会话文件取最近上下文（仅 user/assistant 纯文本，过滤 tool 消息）
            history = agent.session_manager.get_history(session_key)
            recent = [
                {"role": m["role"], "content": str(m["content"])[:600]}
                for m in history[-12:]
                if m.get("role") in ("user", "assistant")
                and not m.get("tool_calls")
                and m.get("content")
            ][-8:]
            if not recent:
                return

            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是资深电商客服坐席助手。以下是一位顾客与客服（AI/人工）的最近对话，"
                        "当前会话已转人工。请针对顾客的最新消息，为人工客服生成一条可直接发送的回复话术。\n"
                        "要求：专业友好、有针对性、可执行（涉及订单/售后时给出明确步骤或结论），"
                        "不超过 120 字。直接输出话术本身，不要任何前缀或解释。"
                    ),
                },
                *recent,
            ]

            response = await provider.chat(
                messages=messages, tools=None, model=getattr(agent, "model", None)
            )
            if (
                response.finish_reason != "error"
                and response.content
                and not response.content.startswith("错误")
            ):
                self.takeover.suggestions[session_key] = response.content.strip()
                print(f"[Gateway] 已为会话 {session_key} 生成人工客服推荐话术")
        except Exception as e:
            print(f"警告: 生成人工客服推荐话术失败 - {e}")

    async def _takeover_timeout_loop(self) -> None:
        """巡检人工接管超时：双方超过设定时间无对话，自动转回 AI 并询问其他需求。"""
        while True:
            try:
                await asyncio.sleep(5)

                for session_key, channel, chat_id in self.takeover.expired(
                    self.human_transfer_timeout
                ):
                    self.takeover.deactivate(session_key)
                    self.takeover.suggestions.pop(session_key, None)
                    minutes = int(self.human_transfer_timeout // 60)
                    message = (
                        f"⏰ 已超过 {minutes} 分钟无对话，人工客服服务结束，"
                        "已为您转回智能客服小纳 🛍️\n"
                        "请问还有其他需要帮助的吗？（商品推荐 / 订单物流 / 售后办理都可以找我～）"
                    )

                    # 先落历史，再重建 Agent（从文件恢复完整历史，含人工接管期间的对话）
                    agent = self._agents.get(session_key)
                    if agent is not None:
                        agent.session_manager.save_message(
                            session_key, {"role": "assistant", "content": message}
                        )
                        self._agents[session_key] = self.agent_factory(session_key)

                    await self.bus.publish_outbound(OutboundMessage(
                        channel=channel,
                        chat_id=chat_id,
                        content=message,
                    ))
                    # 通知顾客端退出人工模式（恢复 AI 思考动画；仅 Web 渠道）
                    if channel == "web":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel,
                            chat_id=chat_id,
                            content=json.dumps(
                                {"type": "human_takeover", "active": False},
                                ensure_ascii=False,
                            ),
                        ))
                    print(f"[Gateway] 会话 {session_key} 人工接管超时，已自动转回 AI")

            except asyncio.CancelledError:
                break

            except Exception as e:
                print(f"警告: 人工接管超时巡检异常 - {e}")
                continue

    async def _dispatch_outbound(self) -> None:
        """
        分发出站消息循环

        while True 循环：
        - 从 bus.consume_outbound() 取回复
        - 根据 msg.channel 找到对应的 Channel
        - 调用 channel.send(msg)
        """
        while True:
            try:
                # 从消息总线消费出站消息
                outbound_msg = await self.bus.consume_outbound()

                # 查找对应的渠道
                channel = self._channel_map.get(outbound_msg.channel)

                if channel is None:
                    print(f"警告: 渠道 '{outbound_msg.channel}' 不存在，无法发送消息")
                    continue

                # 调用渠道发送消息
                try:
                    await channel.send(outbound_msg)

                except Exception as e:
                    # 渠道发送失败，打印日志
                    print(f"警告: 渠道 '{outbound_msg.channel}' 发送失败 - {e}")

            except asyncio.CancelledError:
                # 任务被取消，退出循环
                break

            except Exception as e:
                # 分发循环异常，打印日志并继续
                print(f"警告: 出站分发循环异常 - {e}")
                continue

    async def shutdown(self) -> None:
        """
        关闭网关

        - 遍历 channels 调用 stop()
        - 清空 _agents 缓存
        """
        # 停止所有渠道
        for channel in self.channels:
            try:
                await channel.stop()
            except Exception as e:
                print(f"警告: 渠道 '{channel.name}' 关闭失败 - {e}")

        # 清空 Agent 缓存与会话锁
        self._agents.clear()
        self._session_locks.clear()