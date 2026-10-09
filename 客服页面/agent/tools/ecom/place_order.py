"""下单付款工具：顾客确认购买后一键下单并完成支付（需登录）。

演示环境约定：下单即完成支付，订单状态直接置为「已付款」，
与 data/orders.json 既有数据保持同一存储（query_order / approve_refund
等工具均从该文件读取）。
"""

from datetime import datetime

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, find_good, get_user, load_orders, mask_address,
    mask_phone, now_str, save_orders,
)


class PlaceOrderTool(Tool):
    """为顾客下单并支付：生成「已付款」订单（演示：下单即付款）。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "place_order"

    @property
    def description(self) -> str:
        return (
            "为顾客下单并完成支付（需登录；顾客明确确认购买后调用）。"
            "适用场景：顾客确定要买某件在售商品（'我要买'、'来一件'、'帮我下单'），"
            "已向顾客复述商品名称、数量与总价并收到明确确认。"
            "**确认关联上下文**：当你刚向顾客推荐/询问过某件商品（如'需要我帮您下单袜子吗'），"
            "顾客回复'好的下单'、'确认购买'、'就这个'等确认语时，"
            "商品就是对话中正在讨论的那件，直接使用其 SKU 调用本工具，"
            "严禁再向顾客追问'是哪款商品'，也严禁同时查询物流/订单。"
            "演示环境：下单即完成支付，订单状态为「已付款」（未发货），"
            "随后顾客申请退款时走 approve_refund 的未发货自动退款流程。"
            "收货信息默认沿用顾客最近一单的收货人/电话/地址，"
            "顾客未提供且无历史订单时会返回提示（顾客可补充地址后重试）。"
            "重要：这是资金相关的写操作，调用前必须复述商品、数量、金额"
            "并收到顾客明确确认（如'确认购买'）。"
            "输入：sku（商品SKU）、qty（数量，可选，默认1）、"
            "receiver_name/receiver_phone/address（收货信息，可选，默认沿用最近一单）。"
            "输出：订单号、商品明细、实付金额、订单状态。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sku": {
                    "type": "string",
                    "description": "商品SKU，如 TOWEL-001、CAP-001",
                },
                "qty": {
                    "type": "integer",
                    "description": "购买数量，可选，默认1",
                },
                "receiver_name": {
                    "type": "string",
                    "description": "收货人姓名，可选，默认沿用最近一单",
                },
                "receiver_phone": {
                    "type": "string",
                    "description": "收货人电话，可选，默认沿用最近一单",
                },
                "address": {
                    "type": "string",
                    "description": "收货地址，可选，默认沿用最近一单",
                },
            },
            "required": ["sku"],
        }

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        user = get_user(self._actor)
        if user is None:
            return "账号信息异常，请重新登录。"
        customer_id = user["customer_id"]

        sku = str(kwargs.get("sku") or "").strip().upper()
        if not sku:
            return "请提供商品 SKU（可先用 search_goods 查询），以便完成下单。"
        try:
            qty = int(kwargs.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            qty = 1
        if qty > 99:
            return "单笔订单最多购买 99 件，请调整数量后重试。"

        good = find_good(sku)
        if good is None:
            return f"未找到商品 {sku}，请核对 SKU（可先用 search_goods 查询在售商品）。"
        if good.get("status") and good.get("status") != "在售":
            return f"商品 {good.get('name', sku)} 当前已下架，无法购买。"

        price = good.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            return f"商品 {sku} 价格信息异常，暂时无法下单。"

        stock = good.get("stock")
        try:
            if isinstance(stock, (int, float)) and qty > stock:
                return (
                    f"商品 {good.get('name', sku)} 库存不足（当前库存 {int(stock)} 件），"
                    "请减少数量或换购其他商品。"
                )
        except (TypeError, ValueError):
            pass

        # 收货信息：优先本次传入 → 否则沿用该顾客最近一单
        orders = load_orders()
        mine = [o for o in orders if o["customer_id"] == customer_id]
        mine.sort(key=lambda o: o.get("created_at", ""), reverse=True)
        last = mine[0].get("receiver", {}) if mine else {}

        receiver_name = str(kwargs.get("receiver_name") or "").strip() \
            or last.get("name", "")
        receiver_phone = str(kwargs.get("receiver_phone") or "").strip() \
            or last.get("phone", "")
        address = str(kwargs.get("address") or "").strip() \
            or last.get("address", "")

        if not (receiver_name and receiver_phone and address):
            return (
                "还差收货信息无法下单：请提供收货人姓名、电话与收货地址"
                "（例如：韩梅梅 138xxxx8888 北京市朝阳区xx路xx号），"
                "我会立即为您完成下单。"
            )

        order_id = self._new_order_id(orders)
        total = round(price * qty, 2)
        now = now_str()
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "status": "已付款",
            "items": [{
                "sku": sku,
                "name": good.get("name", sku),
                "qty": qty,
                "price": price,
            }],
            "total": total,
            "receiver": {
                "name": receiver_name,
                "phone": receiver_phone,
                "address": address,
            },
            "created_at": now,
            "paid_at": now,
        }
        orders.append(order)
        save_orders(orders)

        return (
            f"下单成功，已完成支付 ✅\n"
            f"- 订单号：{order_id}\n"
            f"- 商品：{good.get('name', sku)} ×{qty}（¥{price:g}/件）\n"
            f"- 实付金额：¥{total:g}\n"
            f"- 订单状态：已付款（待发货）\n"
            f"- 收货地址：{mask_address(address)} {mask_phone(receiver_phone)}\n"
            "商家会尽快安排发货。收货前如需退款可直接告诉我，"
            "小额订单机器人自动审核秒退。"
        )

    @staticmethod
    def _new_order_id(orders: list) -> str:
        """生成不冲突的订单号：SO + 日期 + 当日序号（如 SO20260909011）。"""
        today = datetime.now().strftime("%Y%m%d")
        prefix = f"SO{today}"
        used = {
            o["order_id"] for o in orders
            if str(o.get("order_id", "")).startswith(prefix)
        }
        seq = 1
        while f"{prefix}{seq:03d}" in used:
            seq += 1
        return f"{prefix}{seq:03d}"
