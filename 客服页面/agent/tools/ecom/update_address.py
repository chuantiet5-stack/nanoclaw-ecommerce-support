"""修改地址工具：修改未签收订单的收货地址（需登录 + 归属校验 + 顾客确认后调用）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, check_order_owner, find_order, get_user,
    load_orders, mask_address, save_orders,
)


class UpdateAddressTool(Tool):
    """修改未签收订单的收货地址。已签收/已完成订单不可修改。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "update_address"

    @property
    def description(self) -> str:
        return (
            "修改订单的收货地址（需登录；未登录时返回登录提示）。"
            "仅适用于未签收的订单（待付款/已付款/已发货），已签收或已完成的订单无法修改。"
            "重要：这是一个写操作，必须先向顾客复述订单号与新地址，"
            "收到顾客明确确认（如回复'确认'）后才能调用。"
            "输入：order_id（订单号）、new_address（新的收货地址）。"
            "输出：修改结果；订单不存在、非本人订单或已签收时返回失败原因。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单号，如 SO20260903004",
                },
                "new_address": {
                    "type": "string",
                    "description": "新的收货地址（省市区+街道门牌）",
                },
            },
            "required": ["order_id", "new_address"],
        }

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        user = get_user(self._actor)
        if user is None:
            return "账号信息异常，请重新登录。"

        order_id = str(kwargs.get("order_id") or "").strip()
        new_address = str(kwargs.get("new_address") or "").strip()

        if not order_id or not new_address:
            return "请提供订单号和新的收货地址，例如：SO20260903004 上海市浦东新区张江路5号。"

        order = find_order(order_id)
        if order is None:
            return f"未找到订单 {order_id}，请核对订单号。"

        denied = check_order_owner(order, user["customer_id"])
        if denied:
            return denied

        if order["status"] in ("已签收", "已完成"):
            return (
                f"订单 {order_id} 已{order['status']}，无法修改收货地址。"
                "如需帮助可以为您创建售后工单。"
            )

        # 写回订单文件
        orders = load_orders()
        for o in orders:
            if o["order_id"] == order_id:
                o["receiver"]["address"] = new_address
                break
        save_orders(orders)

        return (
            f"订单 {order_id} 的收货地址已修改成功：\n"
            f"- 新地址：{mask_address(new_address)}\n"
            "后续物流信息将按新地址派送。"
        )
