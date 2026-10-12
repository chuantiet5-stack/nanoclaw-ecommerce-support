"""工单进度工具：按工单号或订单号查询工单进度（需登录 + 归属校验）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import check_login, get_user, load_tickets


class QueryTicketProgressTool(Tool):
    """按工单号查工单进度；不传时列出当前顾客最近的工单。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "query_ticket_progress"

    @property
    def description(self) -> str:
        return (
            "查询工单处理进度（需登录；未登录时返回登录提示）。"
            "适用场景：顾客询问'我那个工单处理得怎么样了'、凭工单号查询进度。"
            "输入：ticket_id（工单号，可选，如 TK20260901001；"
            "不传则返回顾客最近的工单列表）。"
            "输出：工单状态、处理记录、最新更新时间。"
            "注意：只能查询当前登录顾客本人的工单。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "工单号，如 TK20260901001；不传则返回工单列表",
                },
            },
        }

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        user = get_user(self._actor)
        if user is None:
            return "账号信息异常，请重新登录。"
        customer_id = user["customer_id"]

        ticket_id = str(kwargs.get("ticket_id") or "").strip()
        tickets = load_tickets()

        if ticket_id:
            ticket = next((t for t in tickets if t["ticket_id"] == ticket_id), None)
            if ticket is None:
                return f"未找到工单 {ticket_id}，请核对工单号。"
            if ticket["customer_id"] != customer_id:
                return "无权查询该工单。"
            return self._format(ticket)

        mine = [t for t in tickets if t["customer_id"] == customer_id]
        if not mine:
            return "您暂时没有工单记录。如需售后或投诉可以随时告诉我。"
        mine.sort(key=lambda t: t["created_at"], reverse=True)
        lines = [f"您共有 {len(mine)} 个工单，最近 {min(3, len(mine))} 个："]
        for i, t in enumerate(mine[:3], 1):
            lines.append(
                f"{i}. {t['ticket_id']} 【{t['type']}·{t['status']}】"
                f" — {t['description'][:20]}（{t['created_at'][:10]}创建）"
            )
        lines.append("如需查看某个工单的处理记录，请提供工单号。")
        return "\n".join(lines)

    def _format(self, ticket: dict) -> str:
        lines = [
            f"工单 {ticket['ticket_id']}【{ticket['type']}】",
            f"- 状态：{ticket['status']}（优先级：{ticket['priority']}）",
            f"- 问题描述：{ticket['description']}",
            f"- 关联订单：{ticket.get('order_id') or '无'}",
            f"- 创建时间：{ticket['created_at']}",
            f"- 最新更新：{ticket['updated_at']}",
            "处理记录：",
        ]
        for r in ticket.get("records", []):
            lines.append(f"  · {r['time']} {r['note']}")
        return "\n".join(lines)
