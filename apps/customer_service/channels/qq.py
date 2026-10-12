"""QQ 渠道实现模块。
该模块负责对接 QQ 官方机器人 API，处理消息的接收与发送，
并将其接入到内部的消息总线（MessageBus）系统中。
"""

import asyncio
import re
from typing import Any

import botpy #QQ 官方 SDK
from botpy.message import GroupMessage, C2CMessage

from bus import InboundMessage, MessageBus, OutboundMessage
from .base import Channel

class QQBotClient(botpy.Client):
    """QQ Bot 客户端，继承自 botpy.Client，专门用于处理消息接收事件。"""

    def __init__(self, channel: "QQChannel", *args, **kwargs):
        """初始化 QQ Bot 客户端。

        Args:
            channel: QQChannel 实例，用于将接收到的消息发布到总线
        """
        super().__init__(*args, **kwargs)
        self._channel = channel

    async def on_group_at_message_create(self, message: GroupMessage):
        """处理群内 @机器人 的消息事件。

        Args:
            message: 官方 SDK 提供的群消息对象
        """
        try:
            # 提取消息核心信息
            sender_id = message.author.member_openid  # 发送者的群内唯一ID
            chat_id = message.group_openid            # 群聊的唯一ID
            content = message.content.strip()         # 去除首尾空格的原始消息内容

            # 使用正则表达式去掉消息中的 @Bot 标签（例如 <@!12345>）
            content = re.sub(r"<@!\d+>", "", content).strip()

            # 如果去掉 @ 后内容为空，则直接返回，不做处理
            if not content:
                return

            print(f"[QQChannel] 群消息: sender={sender_id} chat={chat_id} content={content}")

            # 构造标准化的入站消息对象
            inbound = InboundMessage(
                channel="qq",
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                raw={  # 保留原始消息的部分元数据，方便后续扩展或调试
                    "message_id": message.id,
                    "group_openid": chat_id,
                    "msg_type": "group",
                    "timestamp": message.timestamp,
                },
            )

            # 将处理好的消息发布到内部消息总线，供下游 Agent 消费
            await self._channel.bus.publish_inbound(inbound)

        except Exception as e:
            # 异常捕获与打印，防止单条消息处理失败导致整个客户端崩溃
            import traceback
            print(f"[QQChannel] 群消息处理异常: {e}")
            traceback.print_exc()

    async def on_c2c_message_create(self, message: C2CMessage):
        """处理 C2C（用户与机器人单聊）消息事件。

        Args:
            message: 官方 SDK 提供的 C2C 消息对象
        """
        try:
            # 提取消息信息
            sender_id = message.author.user_openid
            chat_id = sender_id  # 单聊场景下，会话ID(chat_id) 等同于对方的用户ID
            content = message.content.strip()

            # 跳过空消息
            if not content:
                return

            print(f"[QQChannel] C2C消息: sender={sender_id} content={content}")

            # 构造标准化的入站消息对象
            inbound = InboundMessage(
                channel="qq",
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                raw={
                    "message_id": message.id,
                    "user_openid": sender_id,
                    "msg_type": "c2c",
                    "timestamp": message.timestamp,
                },
            )

            # 将处理好的消息发布到内部消息总线
            await self._channel.bus.publish_inbound(inbound)

        except Exception as e:
            import traceback
            print(f"[QQChannel] C2C消息处理异常: {e}")
            traceback.print_exc()

class QQChannel(Channel):
    """QQ 渠道主类，负责管理 Bot 的生命周期（启动/停止）以及消息的发送。"""

    def __init__(self, bus: MessageBus, app_id: str, app_secret: str):
        """初始化 QQ 渠道。

        Args:
            bus: 全局消息总线实例
            app_id: QQ 开放平台的机器人 App ID
            app_secret: QQ 开放平台的机器人 App Secret
        """
        super().__init__(name="qq", bus=bus)
        self.app_id = app_id
        self.app_secret = app_secret
        self._bot: QQBotClient | None = None  # 存放 Bot 客户端实例
        self._running = False                 # 运行状态标志位

    async def start(self) -> None:
        """启动 QQ Bot 客户端，建立长连接并监听消息事件。"""
        # 配置 intents（意图），声明我们需要接收的事件类型
        intents = botpy.Intents(
            public_messages=True,  # 开启公域消息（包含群消息和 C2C 消息）
        )

        # 实例化 Bot 客户端
        self._bot = QQBotClient(channel=self, intents=intents)

        self._running = True
        print("[QQChannel] 正在启动 QQ Bot...")

        # 使用异步上下文管理器启动 Bot
        # botpy 的 start() 方法会阻塞直到连接断开
        try:
            async with self._bot:
                await self._bot.start(appid=self.app_id, secret=self.app_secret)
        except Exception as e:
            import traceback
            print(f"[QQChannel] Bot 启动异常: {e}")
            traceback.print_exc()

    async def send(self, message: OutboundMessage) -> None:
        """发送消息到 QQ 用户或群聊。

        Args:
            message: 待发送的出站消息实例
        """
        if self._bot is None or not hasattr(self._bot, 'api'):
            print("[QQChannel] Bot 未初始化，无法发送消息")
            return

        try:
            # 发送策略：由于 OutboundMessage 可能没有明确标识是群聊还是单聊，
            # 这里采用“先尝试 C2C，失败后再尝试群聊”的兜底策略。

            # 1. 优先尝试发送 C2C 私聊消息
            try:
                await self._bot.api.post_c2c_message(
                    openid=message.chat_id,
                    msg_type=0,  # 0 代表纯文本消息
                    msg_id="",   # 回复特定消息时可填入原消息ID，此处留空
                    content=message.content,
                )
                print(f"[QQChannel] C2C消息已发送到 {message.chat_id}")
                return  # 发送成功，直接返回
            except Exception:
                # 如果 C2C 发送失败（例如该 ID 其实是群 ID），捕获异常并继续尝试群消息
                pass

            # 2. 尝试发送群聊消息
            await self._bot.api.post_group_message(
                group_openid=message.chat_id,
                msg_type=0,
                msg_id="",
                content=message.content,
            )
            print(f"[QQChannel] 群消息已发送到 {message.chat_id}")

        except Exception as e:
            import traceback
            print(f"[QQChannel] 发送消息异常: {e}")
            traceback.print_exc()

    async def stop(self) -> None:
        """停止 QQ Bot 客户端连接，释放资源。"""
        self._running = False
        if self._bot is not None:
            self._bot = None
            print("[QQChannel] Bot 已停止")