"""退款审核执行工具：对顾客已确认的退货/退款申请给出最终审核结果。

链路位置：
    check_return_eligibility（判定资格）→ 顾客确认 → approve_refund（本工具，执行审核）

按订单状态分流：
- 已付款（未发货）：小额（≤50元）自动审核直接退款；大额转人工
- 已发货（在途）：小额自动拦截物流并退款（无需顾客寄回）；大额转人工
- 已签收/已完成：
    auto_approve → 调平台退款 API（模拟）→ 退款记录 + 审计 → 秒退
    auto_reject  → 审计 → 告知拒绝依据（质量问题可走例外通道）
    manual       → 自动创建售后工单 + 审计 → 转人工跟进
- 转人工分支（含大额）：文案含「已提交人工审核」，Gateway 据此
  进入人工接管模式并弹窗+声音提醒后台人工客服跟进
"""

from datetime import datetime

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, check_order_owner, find_order, get_user,
    load_logistics, load_orders, load_tickets, new_ticket_id,
    now_str, save_logistics, save_orders, save_tickets,
)
from agent.tools.ecom.refund_policy import (
    append_audit, classify_pre_receipt_refund, classify_refund,
    save_refund_record,
)


class ApproveRefundTool(Tool):
    """执行退款退货审核：小额自动秒退（未发货直退/已发货拦截物流），大额转人工。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "approve_refund"

    @property
    def description(self) -> str:
        return (
            "执行退款退货审核（需登录；必须在顾客明确确认退款/退货后调用）。"
            "适用场景：顾客已确认退款/退货，需要系统给出最终审核结果。"
            "按订单状态自动选择策略：\n"
            "① 已付款（未发货）：小额（≤50元）机器人自动审核后直接自动退款，"
            "大额转人工；\n"
            "② 已发货（在途）：小额自动拦截物流并退款（顾客无需寄回、无需人工），"
            "大额转人工；\n"
            "③ 已签收/已完成：小额+签收7天内+无理由+账号正常自动退款；"
            "超过7天无理由期自动拒绝；质量问题/大额/高风险转人工。\n"
            "注意：这是资金相关的写操作，调用前必须向顾客复述订单号、金额"
            "并收到明确确认（如顾客回复'确认退货'/'确认退款'）。"
            "顾客未说明具体原因时，reason 直接填'无理由退货'，不要反复追问。"
            "转人工分支会自动创建售后工单并提醒后台人工客服跟进，"
            "无需再调用 create_ticket。\n"
            "输入：order_id（订单号）、reason（原因；顾客未说明时填'无理由退货'）。\n"
            "输出：审核结果（通过/拒绝/转人工）、决策依据、退款流水号或工单号。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单号，如 SO20260901001",
                },
                "reason": {
                    "type": "string",
                    "description": "退货原因；顾客未说明时填'无理由退货'（可选）",
                },
            },
            "required": ["order_id"],
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
        # 顾客未说明原因时默认按无理由退货，避免流程卡在"要原因"上
        reason = str(kwargs.get("reason") or "").strip() or "无理由退货"
        if not order_id:
            return "请提供订单号，以便完成审核。"

        order = find_order(order_id)
        if order is None:
            return f"未找到订单 {order_id}，请核对订单号。"
        denied = check_order_owner(order, customer_id)
        if denied:
            return denied

        # 已退款的订单不可重复退款
        if order["status"] == "已退款":
            return (
                f"订单 {order_id} 已完成退款，请勿重复申请。"
                "如退款未到账（一般 1-3 个工作日），可为您创建工单人工跟进。"
            )

        # 待付款：无资金往来，直接取消即可
        if order["status"] == "待付款":
            return (
                f"订单 {order_id} 当前状态为「待付款」，未产生支付无需退款："
                "可以直接取消订单或放弃支付，系统会自动关闭订单。"
            )

        first_item = order["items"][0]["name"] if order["items"] else "商品"
        amount = order["total"]

        # ---- 签收前（已付款未发货 / 已发货在途）：仅退款，不走退货 ----
        # 小额（≤50元）机器人自动审核：未发货直接退款，已发货先拦截物流再退款；
        # 大额或风控异常转人工（文案含「已提交人工审核」，Gateway 据此提醒后台人工）
        if order["status"] in ("已付款", "已发货"):
            decision = classify_pre_receipt_refund(order, customer_id)

            append_audit({
                "time": now_str(),
                "order_id": order_id,
                "customer_id": customer_id,
                "amount": amount,
                "reason": reason,
                "action": decision.action,
                "rule": decision.rule,
                "detail": decision.reason,
                "profile": decision.profile,
            })

            if decision.action == "auto_approve":
                intercepted = (
                    self._intercept_logistics(order_id)
                    if order["status"] == "已发货" else False
                )
                return await self._auto_refund_pre_receipt(
                    order, reason, decision, intercepted
                )

            # manual：大额/风控异常 → 创建售后工单转人工
            ticket_id = self._create_manual_ticket(order, reason, customer_id, decision)
            return (
                f"订单 {order_id}（{first_item}，¥{amount}）的退款申请已提交人工审核\n"
                f"- 转人工依据：{decision.reason}\n"
                f"- 工单号：{ticket_id}\n"
                "- 已提醒人工客服尽快跟进（24 小时内），您可随时回复工单号查询进度。"
            )

        # 仅已签收/已完成订单走退货审核
        if order["status"] not in ("已签收", "已完成"):
            return (
                f"订单 {order_id} 当前状态为「{order['status']}」，暂不支持线上退款审核，"
                "请联系人工客服处理。"
            )

        # ---- 纯规则决策（不依赖 LLM）----
        decision = classify_refund(order, reason, customer_id)
        amount = order["total"]
        first_item = order["items"][0]["name"] if order["items"] else "商品"

        # 审计：无论结果如何都记录，可回溯
        append_audit({
            "time": now_str(),
            "order_id": order_id,
            "customer_id": customer_id,
            "amount": amount,
            "reason": reason,
            "action": decision.action,
            "rule": decision.rule,
            "detail": decision.reason,
            "profile": decision.profile,
        })

        if decision.action == "auto_approve":
            return await self._auto_approve(order, reason, decision)

        if decision.action == "auto_reject":
            return (
                f"订单 {order_id}（{first_item}）退货申请未通过 ❌\n"
                f"- 依据：{decision.reason}\n"
                "- 例外：如商品存在质量问题，不受 7 天限制，"
                "可提供照片/视频为您创建售后工单走质量通道。"
            )

        # manual：自动创建售后工单转人工
        ticket_id = self._create_manual_ticket(order, reason, customer_id, decision)
        return (
            f"订单 {order_id}（{first_item}，¥{amount}）的退货申请已提交人工审核\n"
            f"- 转人工依据：{decision.reason}\n"
            f"- 工单号：{ticket_id}\n"
            "- 客服会在 24 小时内跟进，您可随时回复工单号查询进度。"
        )

    # ---- 分支实现 ----

    async def _auto_approve(self, order: dict, reason: str, decision) -> str:
        """自动通过：调平台退款 API（当前模拟），落退款记录。"""
        refund_no = await self._call_platform_refund_api(order, reason)
        save_refund_record({
            "refund_no": refund_no,
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "amount": order["total"],
            "reason": reason,
            "decided_by": "auto",
            "rule": decision.rule,
            "created_at": now_str(),
        })
        first_item = order["items"][0]["name"] if order["items"] else "商品"
        return (
            f"订单 {order['order_id']}（{first_item}）退货申请已自动通过 ✅\n"
            f"- 退款金额：¥{order['total']}\n"
            f"- 退款流水号：{refund_no}\n"
            f"- 审核依据：{decision.reason}\n"
            "退款将原路返回（一般 1-3 个工作日到账）。"
        )

    async def _call_platform_refund_api(self, order: dict, reason: str) -> str:
        """模拟调用平台"同意退款"API，返回退款流水号。

        落地替换点（按商家所在平台选择其一，需商家授权）：
        - 淘宝/天猫 TOP：taobao.refunds.receive.get 查退款单 + 同意退款接口
        - 拼多多：pdd.refund.agree
        - 京东宙斯：售后单审核接口

        当前模拟实现：生成本地流水号并直接返回成功。
        """
        # TODO: 替换为真实平台 API 调用（httpx.AsyncClient）
        return f"RF{datetime.now().strftime('%Y%m%d%H%M%S%f')[:16]}"

    # ---- 签收前（已付款/已发货）仅退款分支 ----

    async def _auto_refund_pre_receipt(self, order: dict, reason: str,
                                       decision, intercepted: bool) -> str:
        """签收前小额自动退款：未发货直退；已发货先拦截物流再退款。"""
        refund_no = await self._call_platform_refund_api(order, reason)
        save_refund_record({
            "refund_no": refund_no,
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "amount": order["total"],
            "reason": reason,
            "decided_by": "auto",
            "rule": decision.rule,
            "created_at": now_str(),
        })
        self._mark_order_refunded(order["order_id"])
        first_item = order["items"][0]["name"] if order["items"] else "商品"

        if intercepted:
            return (
                f"订单 {order['order_id']}（{first_item}）已自动拦截物流并退款 ✅\n"
                f"- 已通知快递拦截包裹退回商家，您无需任何操作、也无需拒收\n"
                f"- 退款金额：¥{order['total']}\n"
                f"- 退款流水号：{refund_no}\n"
                f"- 审核依据：{decision.reason}\n"
                "退款将原路返回（一般 1-3 个工作日到账）。"
            )
        return (
            f"订单 {order['order_id']}（{first_item}）尚未发货，"
            f"机器人自动审核通过，退款已发起 ✅\n"
            f"- 退款金额：¥{order['total']}\n"
            f"- 退款流水号：{refund_no}\n"
            f"- 审核依据：{decision.reason}\n"
            "退款将原路返回（一般 1-3 个工作日到账）。"
        )

    def _intercept_logistics(self, order_id: str) -> bool:
        """拦截在途物流：在轨迹末尾追加拦截节点。

        无物流单（商家未揽收）时视为拦截成功（不会再发出）。
        """
        logistics = load_logistics()
        traces = logistics.get(order_id)
        if traces is None:
            return True
        traces.append({
            "time": now_str(),
            "location": "商家已通知快递公司拦截",
            "status": "已拦截，包裹退回商家",
        })
        save_logistics(logistics)
        return True

    def _mark_order_refunded(self, order_id: str) -> None:
        """订单状态置为「已退款」。"""
        orders = load_orders()
        for o in orders:
            if o["order_id"] == order_id:
                o["status"] = "已退款"
                break
        save_orders(orders)

    def _create_manual_ticket(self, order: dict, reason: str,
                              customer_id: str, decision) -> str:
        """转人工分支：自动创建售后工单，附决策依据供客服快速判断。"""
        amount = order["total"]
        ticket = {
            "ticket_id": new_ticket_id(),
            "type": "售后",
            "order_id": order["order_id"],
            "customer_id": customer_id,
            "description": (
                f"退款/退货转人工审核：{reason}（金额 ¥{amount}）"
            ),
            "status": "处理中",
            "priority": "加急" if amount > 200 else "普通",
            "created_at": now_str(),
            "updated_at": now_str(),
            "records": [
                {
                    "time": now_str(),
                    "note": f"自动审核转人工（规则 {decision.rule}）：{decision.reason}",
                },
            ],
        }
        tickets = load_tickets()
        tickets.append(ticket)
        save_tickets(tickets)
        return ticket["ticket_id"]
