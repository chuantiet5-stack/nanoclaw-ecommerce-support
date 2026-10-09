"""退货资格工具：判定订单退换货资格（需登录 + 归属校验，可直接调用）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, check_order_owner, days_since, find_order, get_user,
    load_orders, return_deadline,
)


class CheckReturnEligibilityTool(Tool):
    """按订单状态与签收时间判定退换货资格，并给出政策依据与下一步。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "check_return_eligibility"

    @property
    def description(self) -> str:
        return (
            "判定订单是否支持退换货（需登录；未登录时返回登录提示），可直接调用。"
            "适用场景：顾客想退货/换货/退款，先判定资格再决定下一步。"
            "输入：order_id（订单号，可选；不传则自动检查顾客最近签收的订单）、"
            "reason（退货原因，可选）。"
            "输出：是否可退换、政策依据（签收后7天内可无理由退货）、所需材料、下一步建议。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单号，可选；不传则检查最近签收的订单",
                },
                "reason": {
                    "type": "string",
                    "description": "退货原因，可选，如'尺码不合适'",
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

        order_id = str(kwargs.get("order_id") or "").strip()

        if order_id:
            order = find_order(order_id)
            if order is None:
                return f"未找到订单 {order_id}，请核对订单号。"
            denied = check_order_owner(order, customer_id)
            if denied:
                return denied
        else:
            candidates = [
                o for o in load_orders()
                if o["customer_id"] == customer_id and o.get("signed_at")
            ]
            if not candidates:
                return "您暂时没有已签收的订单，无需申请退货。"
            candidates.sort(key=lambda o: o["signed_at"], reverse=True)
            order = candidates[0]

        first_item = order["items"][0]["name"] if order["items"] else "商品"
        status = order["status"]

        if status == "待付款":
            return (
                f"订单 {order['order_id']}（{first_item}）尚未付款，"
                "无需退货：可以直接取消订单或放弃支付，系统会自动关闭订单。"
            )
        if status == "已付款":
            return (
                f"订单 {order['order_id']}（{first_item}）尚未发货，**支持仅退款** ✅\n"
                "- 小额订单（≤50元）：机器人自动审核后秒退，无需人工；\n"
                "- 大额订单：转人工客服跟进。\n"
                "如需退款，请回复'确认退货'（原因可选，不填按无理由处理）。"
            )
        if status == "已发货":
            return (
                f"订单 {order['order_id']}（{first_item}）正在配送途中，"
                f"**支持拦截物流退款** ✅\n"
                "- 小额订单（≤50元）：机器人自动拦截物流并退款，"
                "无需人工、无需您寄回或拒收；\n"
                "- 大额订单：转人工客服跟进。\n"
                "如需退款，请回复'确认退货'（原因可选，不填按无理由处理）。"
            )

        # 已签收 / 已完成：按签收时间判定
        signed_at = order.get("signed_at", "")
        days = days_since(signed_at)
        if days <= 7:
            return (
                f"订单 {order['order_id']}（{first_item}）**支持退货** ✅\n"
                f"- 政策依据：签收后 7 天内可无理由退货（签收于 {signed_at[:10]}，"
                f"今天是第 {days + 1} 天，截止 {return_deadline(signed_at)}）\n"
                "- 所需材料：商品完好、吊牌齐全、包装完整\n"
                "- 下一步：确认申请后我会为您创建退货工单，按指引寄回商品即可。\n"
                "如确认退货，请直接回复'确认退货'（退货原因可选，不填则按无理由退货处理）。"
            )
        return (
            f"订单 {order['order_id']}（{first_item}）**不支持无理由退货** ❌\n"
            f"- 政策依据：签收于 {signed_at[:10]}，已超过 7 天无理由退货期"
            f"（截止 {return_deadline(signed_at)}）\n"
            "- 例外情况：商品存在质量问题不受 7 天限制，"
            "可提供照片/视频为您创建售后工单走质量通道。"
        )
