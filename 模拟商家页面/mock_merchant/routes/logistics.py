"""物流管理 API：查询轨迹 / 创建物流单 / 推进节点 / 签收。

轨迹节点：待发货 → 已揽收 → 到达分拨中心 → 运输中 → 派送中 → 已签收
（advance 每调用一次推进一个节点并记录时间与地点；可模拟派送异常）
"""

import random

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from mock_merchant import auth, db, rules
from mock_merchant import order_service as order_svc
from mock_merchant.faults import is_flag_on
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers

router = APIRouter(prefix="/logistics", tags=["物流管理"])


class CreateLogisticsRequest(BaseModel):
    order_id: str
    carrier: str = "顺丰"


class AdvanceRequest(BaseModel):
    force_exception: bool = False  # 模拟派送异常
    location: str = ""             # 自定义地点（可选）


def create_logistics_record(order_id: str, carrier: str, operator: str) -> dict:
    """为订单创建物流单（发货时自动调用）。"""
    if carrier not in rules.CARRIERS:
        raise AppError(f"快递公司仅支持：{'、'.join(rules.CARRIERS)}", "INVALID_CARRIER", 400)
    existing = db.query_one("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
    if existing:
        raise AppError(f"订单 {order_id} 已存在物流单 {existing['tracking_no']}",
                       "LOGISTICS_EXISTS", 409)
    now = rules.now_str()
    traces = [{"status": "待发货", "location": "商家仓库", "time": now}]
    db.execute(
        """INSERT INTO logistics (order_id, carrier, tracking_no, status,
           current_location, traces, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (order_id, carrier, rules.new_tracking_no(carrier), "待发货", "商家仓库",
         db.dumps(traces), now, now),
    )
    row = db.query_one("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
    order_svc.log_order(order_id, "创建物流单", "", "待发货", operator,
                        f"物流单 {row['tracking_no']}（{carrier}）")
    return row


def sign_logistics(order_id: str, operator: str, remark: str = "包裹已签收") -> dict:
    """物流签收（同步更新订单为已签收）。"""
    row = db.query_one("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
    if not row:
        raise AppError(f"订单 {order_id} 暂无物流信息", "LOGISTICS_NOT_FOUND", 404)
    if row["status"] == "已签收":
        return row
    now = rules.now_str()
    traces = db.loads(row["traces"], [])
    traces.append({"status": "已签收", "location": "本人签收", "time": now})
    db.execute(
        """UPDATE logistics SET status = '已签收', current_location = ?, traces = ?,
           updated_at = ? WHERE order_id = ?""",
        ("本人签收", db.dumps(traces), now, order_id),
    )
    order = db.query_one("SELECT status FROM orders WHERE order_id = ?", (order_id,))
    if order and order["status"] in ("已发货", "运输中"):
        order_svc.change_status(order_id, "已签收", "物流签收", operator, remark,
                                extra_set="signed_at = ?", extra_params=(now,))
    return db.query_one("SELECT * FROM logistics WHERE order_id = ?", (order_id,))


def _get_or_404(order_id: str) -> dict:
    row = db.query_one("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
    if not row:
        raise AppError(f"订单 {order_id} 暂无物流信息（商家尚未发货）",
                       "LOGISTICS_NOT_FOUND", 404)
    return row


def _check_access(principal: dict, order_id: str) -> None:
    if principal["type"] == "customer":
        order = db.query_one("SELECT customer_id FROM orders WHERE order_id = ?", (order_id,))
        if not order or order["customer_id"] != principal["subject_id"]:
            raise AppError("无权查询该订单的物流", "ORDER_ACCESS_DENIED", 403)


@router.get("/{order_id}")
def get_logistics(order_id: str,
                  principal: dict = Depends(auth.require_scope("logistics:read"))):
    """查询物流轨迹（traces 结构与 V4 logistics.json 兼容：status/location/time）。"""
    if is_flag_on("logistics_query_fail"):
        raise AppError("模拟故障：物流查询失败，请稍后重试", "LOGISTICS_QUERY_FAILED", 503)
    _check_access(principal, order_id)
    return ok(helpers.logistics_dict(_get_or_404(order_id)))


@router.post("/create")
def create_logistics(body: CreateLogisticsRequest,
                     principal: dict = Depends(auth.require_scope("logistics:write"))):
    """手动创建物流单（一般由发货接口自动创建，此处用于演示）。"""
    order = order_svc.get_order(body.order_id)
    if order["status"] in ("待付款", "已取消", "已退款"):
        raise AppError(
            f"订单当前状态为「{order['status']}」，不允许创建物流单",
            "INVALID_STATUS_TRANSITION", 409,
        )
    row = create_logistics_record(body.order_id, body.carrier, principal["username"])
    helpers.log_op(principal, "创建物流单", f"logistics:{body.order_id}",
                   f"物流单 {row['tracking_no']}（{body.carrier}）")
    return ok(helpers.logistics_dict(row), "物流单创建成功", 201)


@router.post("/{order_id}/advance")
def advance_logistics(order_id: str, body: AdvanceRequest | None = None,
                      principal: dict = Depends(auth.require_scope("logistics:write"))):
    """推进物流到下一节点（演示用：每次调用推进一个节点并记录时间地点）。"""
    row = _get_or_404(order_id)
    if row["status"] == "已签收":
        raise AppError("物流已签收，无法继续推进", "LOGISTICS_FINISHED", 409)
    body = body or AdvanceRequest()

    current = row["status"]
    # 派送异常 → 下一次推进重新派送
    if current == "派送异常" or (body.force_exception and current == "派送中"):
        next_node = "派送异常"
        location = body.location or random.choice(rules.NODE_LOCATIONS["派送异常"])
    else:
        next_node = rules.next_logistics_node(current)
        if next_node is None:
            raise AppError("物流已到达终点节点", "LOGISTICS_FINISHED", 409)
        location = body.location or random.choice(
            rules.NODE_LOCATIONS.get(next_node, [next_node])
        )

    now = rules.now_str()
    traces = db.loads(row["traces"], [])
    traces.append({"status": next_node, "location": location, "time": now})
    db.execute(
        """UPDATE logistics SET status = ?, current_location = ?, traces = ?,
           updated_at = ? WHERE order_id = ?""",
        (next_node, location, db.dumps(traces), now, order_id),
    )

    # 订单状态联动：运输中 / 已签收
    operator = principal["username"]
    if next_node == "运输中":
        order = db.query_one("SELECT status FROM orders WHERE order_id = ?", (order_id,))
        if order and order["status"] == "已发货":
            order_svc.change_status(order_id, "运输中", "物流运输", operator, "包裹运输中")
    elif next_node == "已签收":
        sign_logistics(order_id, operator)

    helpers.log_op(principal, "推进物流", f"logistics:{order_id}",
                   f"{current} → {next_node}（{location}）")
    return ok(helpers.logistics_dict(
        db.query_one("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
    ), f"物流已推进至「{next_node}」")


@router.post("/{order_id}/sign")
def sign(order_id: str,
         principal: dict = Depends(auth.require_scope("logistics:write"))):
    """直接签收（跳过剩余节点，订单同步已签收）。"""
    row = _get_or_404(order_id)
    if row["status"] == "已签收":
        raise AppError("物流已签收，无需重复操作", "LOGISTICS_FINISHED", 409)
    result = sign_logistics(order_id, principal["username"])
    helpers.log_op(principal, "物流签收", f"logistics:{order_id}", "物流已签收")
    return ok(helpers.logistics_dict(result), "物流已签收")
