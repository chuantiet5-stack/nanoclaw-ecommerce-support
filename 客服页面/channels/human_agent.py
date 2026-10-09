"""人工客服通知适配器（生产落地 P2-3.2 步骤 1）。

转人工闭环的"通知"环节：EscalateToHumanTool 创建工单后，
通过 Notifier 通知人工客服团队（飞书群机器人 webhook 等）。

当前为新增模块，尚未注入 EscalateToHumanTool（无真实 webhook 配置，
注入属于行为变更，待配置 FEISHU_AGENT_WEBHOOK_URL 后启用）。
"""

from abc import ABC, abstractmethod


class HumanAgentNotifier(ABC):
    """人工客服通知接口。"""

    @abstractmethod
    async def notify(self, ticket_id: str, customer_id: str,
                     summary: str, channel: str) -> bool:
        """通知人工客服有新工单。"""
        ...


class FeishuNotifier(HumanAgentNotifier):
    """通过飞书群机器人 webhook 通知客服团队。"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def notify(self, ticket_id, customer_id, summary, channel):
        import httpx

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "🔔 新工单待处理"}
                },
                "elements": [
                    {"tag": "div", "text": {
                        "content": f"**工单号**：{ticket_id}", "tag": "lark_md"}},
                    {"tag": "div", "text": {
                        "content": f"**用户**：{customer_id}", "tag": "lark_md"}},
                    {"tag": "div", "text": {
                        "content": f"**来源**：{channel}", "tag": "lark_md"}},
                    {"tag": "div", "text": {
                        "content": f"**摘要**：{summary}", "tag": "lark_md"}},
                ],
            },
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(self.webhook_url, json=payload)
            return resp.status_code == 200
