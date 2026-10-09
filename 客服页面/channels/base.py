"""
渠道适配器抽象基类模块

Channel 定义所有渠道适配器的统一接口，支持多种消息平台接入。
每个渠道适配器负责：
- 接收用户消息.inbound_queue
- 从 MessageBus.outbound_q → 发布到 MessageBusueue 消费 Agent 回复 → 发送给用户

使用示例：
    class FeishuChannel(Channel):
        def __init__(self, bus: MessageBus):
            super().__init__(name="feishu", bus=bus)

        async def start(self):
            # 监听飞书 Webhook，收到消息后发布到 bus
            ...

        async def send(self, message: OutboundMessage):
            # 调用飞书 API 发送消息
            ...
"""

from abc import ABC, abstractmethod

from bus.queue import MessageBus, OutboundMessage


class Channel(ABC):
    """
    渠道适配器抽象基类

    定义渠道的生命周期和消息发送接口。
    所有渠道适配器必须实现 start() 和 send() 方法。

    Attributes:
        name: 渠道名称，如 "feishu"、"qq"、"web"、"cli"
        bus: 消息总线实例，用于发布/消费消息
    """

    def __init__(self, name: str, bus: MessageBus) -> None:
        """
        Args:
            name: 渠道名称
            bus: 消息总线实例
        """
        self.name = name
        self.bus = bus

    @abstractmethod
    async def start(self) -> None:
        """
        启动渠道适配器

        渠道的生命周期入口，通常是一个无限循环：
        - 监听用户消息 → 发布到 bus.inbound_queue
        - 或从 bus.outbound_queue 消费回复 → 发送给用户

        该方法在后台任务中运行，不应阻塞主线程。
        """
        ...

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        """
        发送出站消息给用户

        从 bus.outbound_queue 消费到消息后，调用此方法发送给用户。
        具体实现依赖各渠道的 API（如飞书 Webhook、QQ HTTP API）。

        Args:
            message: 出站消息实例
        """
        ...

    async def stop(self) -> None:
        """
        停止渠道适配器（可选）

        清理资源、关闭连接等。默认实现为空，可由子类覆盖。
        """
        pass