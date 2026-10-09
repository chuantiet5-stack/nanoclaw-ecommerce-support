"""订单查询工具：查询当前登录顾客的订单（需登录 + 归属校验）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, check_order_owner, find_order, get_user,
    load_orders, mask_address, mask_name, mask_phone,
)


class QueryOrderTool(Tool):
    """按订单号查订单详情；不传订单号时返回当前顾客的最近订单列表。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "query_order"

    @property
    def description(self) -> str:
        return (
            "查询当前登录顾客的订单（需登录；未登录时返回登录提示）。"
            "适用场景：顾客询问订单状态、订单详情、'我的订单'。"
            "输入：order_id（订单号，如 SO20260901001；不传则返回顾客最近的订单列表）。"
            "输出：订单状态、商品明细、金额、收货信息（脱敏）、下单时间。"
            "注意：只能查询当前登录顾客本人的订单，他人订单返回无权提示。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单号，如 SO20260901001；不传则返回订单列表",
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

        # 明细模式
        if order_id:
            order = find_order(order_id)
            if order is None:
                return f"未找到订单 {order_id}，请核对订单号。"
            denied = check_order_owner(order, customer_id)
            if denied:
                return denied
            return self._format_detail(order)

        # 列表模式
        orders = [o for o in load_orders() if o["customer_id"] == customer_id]
        if not orders:
            return "您暂时没有订单记录。"
        orders.sort(key=lambda o: o["created_at"], reverse=True)
        lines = [f"您共有 {len(orders)} 个订单，最近 {min(5, len(orders))} 个："]
        for i, o in enumerate(orders[:5], 1):
            first = o["items"][0]["name"] if o["items"] else "商品"
            extra = f"等{len(o['items'])}件" if len(o["items"]) > 1 else ""
            lines.append(
                f"{i}. {o['order_id']} 【{o['status']}】¥{o['total']}"
                f" — {first}{extra}（{o['created_at'][:10]}下单）"
            )
        lines.append("如需查看某笔订单的详情，请提供订单号。")
        return "\n".join(lines)

    def _format_detail(self, order: dict) -> str:
        items = "、".join(
            f"{it['name']} ×{it['qty']}（¥{it['price']}/件）"
            for it in order["items"]
        )
        receiver = order.get("receiver", {})
        lines = [
            f"订单 {order['order_id']}【{order['status']}】",
            f"- 商品：{items}",
            f"- 实付金额：¥{order['total']}",
            f"- 下单时间：{order['created_at']}",
        ]
        if receiver:
            lines.append(
                f"- 收货人：{mask_name(receiver.get('name', ''))}"
                f" {mask_phone(receiver.get('phone', ''))}"
            )
            lines.append(f"- 收货地址：{mask_address(receiver.get('address', ''))}")
        if order.get("signed_at"):
            lines.append(f"- 签收时间：{order['signed_at']}")
        return "\n".join(lines)
