"""
CLI 渠道适配器模块

CLIChannel 提供命令行交互界面，支持用户通过终端与 Agent 对话。
特性：
- 支持命令：/exit（退出）、/clear（清空历史）、/tools（查看工具列表）
- 使用 asyncio.Event 同步输入和回复
- input() 用 asyncio.to_thread 包装，避免阻塞事件循环

使用示例：
    bus = MessageBus()
    cli_channel = CLIChannel(bus)

    # 注入工具列表和清空回调（由 main.py 完成）
    cli_channel.tool_names = ["read_file", "write_file"]
    cli_channel._clear_callback = lambda: print("已清空")

    # 启动渠道
    asyncio.create_task(cli_channel.start())
"""

import asyncio
from bus.queue import MessageBus, InboundMessage, OutboundMessage
from channels.base import Channel


class CLIChannel(Channel):
    """
    CLI 渠道适配器

    提供命令行交互界面，监听用户输入并发布到 MessageBus。
    """

    def __init__(self, bus: MessageBus) -> None:
        """
        Args:
            bus: 消息总线实例
        """
        super().__init__(name="cli", bus=bus)

        # 用于同步"等待回复完成"
        self._response_event = asyncio.Event()

        # 预留属性，由 main.py 在创建后注入
        self.tool_names: list[str] = []
        self._clear_callback: callable | None = None

    async def start(self) -> None:
        """
        启动 CLI 交互循环

        循环读取用户输入，支持命令和普通对话。
        正常输入发布到 bus.inbound_queue，然后等待回复完成。
        """
        print("\n开始对话（输入 /exit 退出）")

        while True:
            try:
                # 用 asyncio.to_thread 包装 input()，避免阻塞事件循环
                user_input = await asyncio.to_thread(input, "\n你> ")
                user_input = user_input.strip()

                # 空输入跳过
                if not user_input:
                    continue

                # 处理命令
                if user_input == "/exit":
                    print("再见！")
                    break

                if user_input == "/clear":
                    # 调用清空回调（如果有）
                    if self._clear_callback:
                        self._clear_callback()
                    print("对话历史已清空")
                    continue

                if user_input == "/tools":
                    # 打印工具列表
                    print(f"可用工具: {', '.join(self.tool_names)}")
                    continue

                # 正常输入 → 构造入站消息
                inbound_msg = InboundMessage(
                    channel="cli",
                    sender_id="local",
                    chat_id="direct",
                    content=user_input,
                )

                # 发布到消息总线
                await self.bus.publish_inbound(inbound_msg)

                # 重置 Event，等待回复完成
                self._response_event.clear()
                await self._response_event.wait()

            except KeyboardInterrupt:
                # Ctrl+C 优雅退出
                print("\n\n再见！")
                break

            except EOFError:
                # 输入结束（如管道输入完毕）
                print("\n再见！")
                break

    async def send(self, message: OutboundMessage) -> None:
        """
        发送回复给用户

        打印 Agent 回复，然后通知 start() 可以继续读取下一个输入。

        Args:
            message: 出站消息实例
        """
        # 打印回复
        print(f"\n🤖 {message.content}")

        # 通知 start() 可以继续了
        self._response_event.set()