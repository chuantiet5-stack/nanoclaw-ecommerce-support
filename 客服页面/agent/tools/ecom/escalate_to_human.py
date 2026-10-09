"""兜底转人工工具：自动生成带会话摘要与优先级的人工工单（需登录）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, get_user, load_tickets, new_ticket_id, now_str, save_tickets,
)


class EscalateToHumanTool(Tool):
    """兜底转人工：创建 type=人工 的工单，携带摘要、原因与优先级。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "escalate_to_human"

    @property
    def description(self) -> str:
        return (
            "兜底转人工：为顾客创建人工客服工单（需登录；未登录时返回登录提示）。"
            "适用场景（出现任一即调用，替代继续自行回答）：\n"
            "1. 顾客明确要求转人工（'转人工'、'找真人客服'、'我要投诉到底'）；\n"
            "2. 同一问题顾客连续 2 次表示未解决或不满意；\n"
            "3. 工具无法解决且顾客坚持（订单查无、退款金额争议等超出权限）；\n"
            "4. 顾客情绪激动、提及投诉升级等风险信号。\n"
            "一般性的售后申请/投诉受理请用 create_ticket，不要用本工具。"
            "输入：summary（顾客问题与已尝试处理的摘要）、reason（转人工原因，可选）、"
            "priority（优先级：普通/高/紧急，可选，默认普通）。"
            "输出：人工工单号、优先级、预计响应提示。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "顾客问题与已尝试的处理，供人工客服快速接手",
                },
                "reason": {
                    "type": "string",
                    "description": "转人工原因，如'顾客明确要求'、'连续未解决'",
                },
                "priority": {
                    "type": "string",
                    "enum": ["普通", "高", "紧急"],
                    "description": "优先级，可选，默认普通",
                },
            },
            "required": ["summary"],
        }

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        user = get_user(self._actor)
        if user is None:
            return "账号信息异常，请重新登录。"

        summary = str(kwargs.get("summary") or "").strip()
        reason = str(kwargs.get("reason") or "").strip() or "顾客要求人工服务"
        priority = str(kwargs.get("priority") or "").strip()
        if priority not in ("普通", "高", "紧急"):
            priority = "普通"

        if not summary:
            return "请提供顾客问题的简要描述，以便人工客服快速接手。"

        tickets = load_tickets()
        ticket = {
            "ticket_id": new_ticket_id(),
            "type": "人工",
            "order_id": None,
            "customer_id": user["customer_id"],
            "description": f"[转人工] 原因：{reason}。摘要：{summary}",
            "status": "处理中",
            "priority": priority,
            "created_at": now_str(),
            "updated_at": now_str(),
            "records": [
                {"time": now_str(), "note": f"智能客服转接人工（{reason}），等待客服接入"}
            ],
        }
        tickets.append(ticket)
        save_tickets(tickets)

        eta = {"普通": "30 分钟内", "高": "10 分钟内", "紧急": "5 分钟内"}[priority]
        return (
            f"已为您转接人工客服 👩‍💼\n"
            f"- 工单号：{ticket['ticket_id']}\n"
            f"- 优先级：{priority}\n"
            f"- 预计响应：{eta}会有客服与您联系\n"
            f"- 问题摘要：{summary[:80]}\n"
            "您可以随时凭工单号查询处理进度。"
        )
