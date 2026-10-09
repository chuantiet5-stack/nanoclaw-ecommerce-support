"""物流查询工具：按订单号查询物流轨迹（需登录 + 归属校验）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, check_order_owner, find_order, get_user,
    load_logistics, load_orders,
)

# 已签收/已完成的订单状态：这类包裹不推送给顾客（仅后台可见）
SIGNED_STATUSES = ("已签收", "已完成")


def _is_signed(order: dict) -> bool:
    """判断订单是否已签收：signed_at 非空，或状态为已签收/已完成。"""
    return bool(order.get("signed_at")) or order.get("status") in SIGNED_STATUSES


class QueryLogisticsTool(Tool):
    """按订单号查物流轨迹；不传订单号时自动选取顾客最近的在途订单。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "query_logistics"

    @property
    def description(self) -> str:
        return (
            "按订单号查询物流轨迹（需登录；未登录时返回登录提示）。"
            "适用场景：顾客明确询问物流/包裹/快递相关问题"
            "（'包裹到哪了'、'什么时候到'、'发货了吗'）。"
            "**仅在顾客当前问题与物流直接相关时才调用**；"
            "顾客在咨询商品、推荐、比价、确认下单时严禁调用本工具"
            "（这些场景只调用 search_goods / get_goods_detail / place_order）。"
            "输入：order_id（订单号，可选；不传则列出顾客所有【未签收】包裹的物流轨迹）。"
            "输出：每个未签收包裹的最新节点、完整轨迹时间线；未发货的包裹提示待发货；"
            "派送异常件给出处理建议。已签收的包裹不在此列表（仅后台可见）。"
            "注意：只能查询当前登录顾客本人的订单。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单号，如 SO20260902003；不传则查询最近的在途订单",
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
        logistics = load_logistics()

        if order_id:
            order = find_order(order_id)
            if order is None:
                return f"未找到订单 {order_id}，请核对订单号。"
            denied = check_order_owner(order, customer_id)
            if denied:
                return denied
            # 已签收订单：简短告知签收结果，完整轨迹仅后台可见
            if _is_signed(order):
                signed_at = order.get("signed_at") or order["status"]
                return f"订单 {order_id} 已于 {signed_at} 签收，不在物流跟踪中。"
            tracks = logistics.get(order_id)
            if not tracks:
                return (
                    f"订单 {order_id}（{order['status']}）暂无物流信息，"
                    "商家正在安排发货，请耐心等待。"
                )
            return self._format(order_id, tracks)

        # 未指定订单：列出所有【未签收】包裹的物流轨迹（已签收仅后台可见）
        orders = [o for o in load_orders() if o["customer_id"] == customer_id]
        unsigned = [o for o in orders if not _is_signed(o)]
        if not unsigned:
            return "您目前没有待收货的包裹（已签收的包裹可在后台订单中查看）。"

        unsigned.sort(key=lambda o: o["created_at"], reverse=True)
        lines = [f"您共有 {len(unsigned)} 个待收货包裹，物流状态如下："]
        for i, o in enumerate(unsigned, 1):
            item_name = o["items"][0]["name"] if o["items"] else "商品"
            extra = f"等{len(o['items'])}件" if len(o["items"]) > 1 else ""
            tracks = logistics.get(o["order_id"])
            if not tracks:
                lines.append(
                    f"\n{i}. {o['order_id']}【{o['status']}】{item_name}{extra}"
                    " — 商家尚未发货，请耐心等待。"
                )
                continue
            latest = tracks[-1]
            lines.append(f"\n{i}. 订单 {o['order_id']}【{o['status']}】{item_name}{extra}")
            lines.append(
                f"   最新节点：{latest['status']}（{latest['location']}，{latest['time']}）"
            )
            lines.append("   轨迹时间线：")
            for t in tracks:
                lines.append(f"     · {t['time']} {t['location']} — {t['status']}")
            if "异常" in latest["status"]:
                lines.append(
                    "   ⚠️ 检测到派送异常，建议保持电话畅通等待派送员联系；"
                    "如需人工跟进，可为您创建工单处理。"
                )
        return "\n".join(lines)

    def _format(self, order_id: str, tracks: list) -> str:
        latest = tracks[-1]
        lines = [
            f"订单 {order_id} 的物流信息：",
            f"- 最新节点：{latest['status']}（{latest['location']}，{latest['time']}）",
            "轨迹时间线：",
        ]
        for t in tracks:
            lines.append(f"  · {t['time']} {t['location']} — {t['status']}")

        # 异常件处理建议
        if "异常" in latest["status"]:
            lines.append(
                "⚠️ 检测到派送异常，建议：保持电话畅通等待派送员联系；"
                "如需人工跟进，可以为您创建工单处理。"
            )
        elif latest["status"] == "已签收":
            lines.append("包裹已签收，如对商品有任何问题随时找我～")
        return "\n".join(lines)
