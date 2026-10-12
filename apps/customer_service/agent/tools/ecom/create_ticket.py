"""创建工单工具：创建售后/投诉工单（需登录 + 顾客确认后调用）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, get_user, load_tickets, new_ticket_id, now_str, save_tickets,
)


class CreateTicketTool(Tool):
    """创建售后或投诉工单，写回 tickets.json。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "create_ticket"

    @property
    def description(self) -> str:
        return (
            "创建售后或投诉工单（需登录；未登录时返回登录提示）。"
            "适用场景：顾客确认申请退货/换货、商品质量问题、投诉受理（一般性投诉）。"
            "注意：这是写操作，必须在向顾客复述工单内容并收到明确确认后才能调用；"
            "顾客要求转人工时请改用 escalate_to_human 工具。"
            "输入：type（工单类型：售后/投诉）、order_id（关联订单号，可选）、"
            "description（问题描述）。"
            "输出：工单号、类型、处理时限提示。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["售后", "投诉"],
                    "description": "工单类型：售后（退货/换货/维修）或 投诉",
                },
                "order_id": {
                    "type": "string",
                    "description": "关联订单号，可选",
                },
                "description": {
                    "type": "string",
                    "description": "问题描述，如'鞋头开胶，申请退货'",
                },
            },
            "required": ["type", "description"],
        }

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        user = get_user(self._actor)
        if user is None:
            return "账号信息异常，请重新登录。"

        ticket_type = str(kwargs.get("type") or "").strip()
        description = str(kwargs.get("description") or "").strip()
        order_id = str(kwargs.get("order_id") or "").strip() or None

        if ticket_type not in ("售后", "投诉"):
            return "工单类型仅支持：售后、投诉。"
        if not description:
            return "请描述一下遇到的问题，以便客服更快处理。"

        tickets = load_tickets()
        ticket = {
            "ticket_id": new_ticket_id(),
            "type": ticket_type,
            "order_id": order_id,
            "customer_id": user["customer_id"],
            "description": description,
            "status": "处理中",
            "priority": "普通",
            "created_at": now_str(),
            "updated_at": now_str(),
            "records": [{"time": now_str(), "note": "工单已创建，等待客服受理"}],
        }
        tickets.append(ticket)
        save_tickets(tickets)

        sla = "24 小时内" if ticket_type == "售后" else "12 小时内"
        return (
            f"{ticket_type}工单已创建 ✅\n"
            f"- 工单号：{ticket['ticket_id']}\n"
            f"- 关联订单：{order_id or '无'}\n"
            f"- 处理时限：{sla}会有客服跟进\n"
            f"您可随时回复工单号查询进度。"
        )
