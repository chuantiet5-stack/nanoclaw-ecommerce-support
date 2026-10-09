"""售后管理 API：列表 / 详情 / 申请 / 审核 / 商家收货 / 完成。

售后类型：仅退款、退货退款、换货、维修、投诉
状态流转：待审核 →（审核通过：仅退款→退款中；退货退款/换货/维修→待寄回；
投诉→审核通过）→ 商家收货 → 已完成；或 待审核 → 审核驳回 / 已关闭
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

import config
from mock_merchant import auth, db, rules
from mock_merchant import order_service as order_svc
from mock_merchant import refund_policy
from mock_merchant.faults import is_flag_on
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers
from mock_merchant.routes import refunds as refund_routes

router = APIRouter(prefix="/after-sales", tags=["售后管理"])


class AfterSaleCreate(BaseModel):
    order_id: str
    customer_id: str
    type: str  # 仅退款/退货退款/换货/维修/投诉
    reason: str = Field(min_length=1)
    description: str = ""
    amount: float = Field(default=0, ge=0, description="涉及金额，默认订单实付")


class ReviewRequest(BaseModel):
    remark: str = ""


def _get_after_sale(after_sale_id: str) -> dict:
    row = db.query_one(
        "SELECT * FROM after_sales WHERE after_sale_id = ?", (after_sale_id,)
    )
    if not row:
        raise AppError(f"售后单 {after_sale_id} 不存在", "AFTER_SALE_NOT_FOUND", 404)
    return row


def _check_access(principal: dict, after_sale: dict) -> None:
    if principal["type"] == "customer" and principal["subject_id"] != after_sale["customer_id"]:
        raise AppError("无权访问该售后单", "AFTER_SALE_ACCESS_DENIED", 403)


def _change_status(after_sale_id: str, to_status: str, action: str, operator: str,
                   remark: str = "") -> dict:
    row = _get_after_sale(after_sale_id)
    db.execute(
        "UPDATE after_sales SET status = ?, updated_at = ? WHERE after_sale_id = ?",
        (to_status, rules.now_str(), after_sale_id),
    )
    db.execute(
        """INSERT INTO after_sale_logs (after_sale_id, action, from_status, to_status,
           operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (after_sale_id, action, row["status"], to_status, operator, remark, rules.now_str()),
    )
    return _get_after_sale(after_sale_id)


def _detail(row: dict) -> dict:
    data = helpers.after_sale_dict(row)
    logs = db.query(
        "SELECT * FROM after_sale_logs WHERE after_sale_id = ? ORDER BY id ASC",
        (row["after_sale_id"],),
    )
    data["logs"] = [
        {"action": l["action"], "from_status": l["from_status"],
         "to_status": l["to_status"], "operator": l["operator"],
         "remark": l["remark"], "created_at": l["created_at"]}
        for l in logs
    ]
    if row["refund_id"]:
        refund = db.query_one("SELECT * FROM refunds WHERE refund_id = ?", (row["refund_id"],))
        if refund:
            data["refund"] = helpers.refund_dict(refund)
    order = db.query_one("SELECT * FROM orders WHERE order_id = ?", (row["order_id"],))
    if order:
        data["order_status"] = order["status"]
        data["order_total"] = order["total"]
    return data


@router.get("")
def list_after_sales(
    status: str = Query(default=""),
    type: str = Query(default=""),
    order_id: str = Query(default=""),
    customer_id: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("aftersales:read")),
):
    """售后单列表。顾客 Token 自动只返回本人售后单。"""
    if principal["type"] == "customer":
        customer_id = principal["subject_id"]
    where, params = ["1=1"], []
    if status:
        where.append("status = ?")
        params.append(status)
    if type:
        where.append("type = ?")
        params.append(type)
    if order_id:
        where.append("order_id = ?")
        params.append(order_id)
    if customer_id:
        where.append("customer_id = ?")
        params.append(customer_id)
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM after_sales WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM after_sales WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.after_sale_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


@router.get("/{after_sale_id}")
def get_after_sale(after_sale_id: str,
                   principal: dict = Depends(auth.require_scope("aftersales:read"))):
    """售后单详情（含审核日志、关联退款与订单状态）。"""
    row = _get_after_sale(after_sale_id)
    _check_access(principal, row)
    return ok(_detail(row))


@router.post("")
def create_after_sale(body: AfterSaleCreate,
                      principal: dict = Depends(auth.require_scope("aftersales:create"))):
    """顾客申请售后（校验订单存在 / 归属 / 售后期限 / 类型 / 重复申请 / 订单状态）。"""
    if is_flag_on("duplicate_request"):
        raise AppError("模拟故障：重复请求被拦截", "DUPLICATE_REQUEST", 409)
    if body.type not in rules.AFTER_SALE_TYPES:
        raise AppError(
            f"售后类型仅支持：{'、'.join(rules.AFTER_SALE_TYPES)}", "INVALID_TYPE", 400)

    order = order_svc.get_order(body.order_id)
    if order["customer_id"] != body.customer_id:
        raise AppError("订单不属于该顾客，无法申请售后", "ORDER_OWNERSHIP_MISMATCH", 403)
    if principal["type"] == "customer" and principal["subject_id"] != body.customer_id:
        raise AppError("无权为其他顾客申请售后", "FORBIDDEN", 403)
    if order["status"] not in rules.AFTER_SALE_ALLOWED_ORDER_STATUS:
        raise AppError(
            f"订单当前状态为「{order['status']}」，不允许申请售后",
            "INVALID_STATUS_TRANSITION", 409,
        )

    # 售后期限：已签收订单超出 7 天无理由期则拒绝（质量问题与投诉除外）
    if order["signed_at"] and body.type != "投诉":
        days = rules.days_since(order["signed_at"])
        is_quality = any(tag in body.reason for tag in config.QUALITY_TAGS)
        if days > config.RETURN_WINDOW_DAYS and not is_quality:
            raise AppError(
                f"已超过售后期限：签收已 {days} 天（无理由退货期 {config.RETURN_WINDOW_DAYS} 天，"
                f"截止 {rules.calc_return_deadline(order['signed_at'])}）",
                "AFTER_SALE_EXPIRED", 409,
            )

    # 同一订单是否已有进行中的售后
    ongoing = db.query_one(
        "SELECT after_sale_id FROM after_sales WHERE order_id = ? AND status IN "
        "(?,?,?,?,?,?)",
        (order["order_id"], *rules.ONGOING_AFTER_SALE_STATUS),
    )
    if ongoing:
        raise AppError(
            f"订单已存在进行中的售后单 {ongoing['after_sale_id']}，请勿重复申请",
            "AFTER_SALE_DUPLICATED", 409,
        )

    amount = round(body.amount or order["total"], 2)
    after_sale_id = rules.new_after_sale_id()
    now = rules.now_str()
    db.execute(
        """INSERT INTO after_sales (after_sale_id, order_id, customer_id, type, status,
           reason, description, amount, created_at, updated_at)
           VALUES (?,?,?,?, '待审核', ?,?,?,?,?)""",
        (after_sale_id, order["order_id"], body.customer_id, body.type, body.reason,
         body.description or body.reason, amount, now, now),
    )
    db.execute(
        """INSERT INTO after_sale_logs (after_sale_id, action, from_status, to_status,
           operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (after_sale_id, "创建售后单", "", "待审核", body.customer_id, body.reason, now),
    )

    # 订单联动：资金类（仅退款/退货退款）→ 退款中；换货/维修 → 售后中；投诉 → 不变
    operator = principal["username"]
    if body.type in rules.REFUND_LIKE_TYPES:
        order_svc.start_refund(order["order_id"], operator, f"售后单 {after_sale_id}")
    elif body.type in ("换货", "维修"):
        order_svc.start_after_sale(order["order_id"], operator, f"售后单 {after_sale_id}")

    helpers.log_op(principal, "申请售后", f"after_sales:{after_sale_id}",
                   f"订单 {order['order_id']} 申请{body.type}（{body.reason}）")
    return ok(_detail(_get_after_sale(after_sale_id)), "售后申请已提交", 201)


@router.post("/{after_sale_id}/approve")
def approve(after_sale_id: str, body: ReviewRequest | None = None,
            principal: dict = Depends(auth.require_scope("aftersales:write"))):
    """审核通过：仅退款→退款中（自动创建退款单走风控）；
    退货退款/换货/维修→待寄回；投诉→审核通过。"""
    row = _get_after_sale(after_sale_id)
    if row["status"] != "待审核":
        raise AppError(
            f"售后单当前状态为「{row['status']}」，不允许审核", "INVALID_STATUS_TRANSITION", 409)
    operator = principal["username"]
    remark = (body.remark if body else "") or "审核通过"
    order = order_svc.get_order(row["order_id"])

    if row["type"] == "仅退款":
        _change_status(after_sale_id, "退款中", "审核通过", operator, remark)
        refund = refund_routes.create_refund_with_review(
            order, row["reason"], row["amount"] or order["total"],
            after_sale_id, operator)
        db.execute(
            "UPDATE after_sales SET refund_id = ?, updated_at = ? WHERE after_sale_id = ?",
            (refund["refund_id"], rules.now_str(), after_sale_id),
        )
        # 风控自动驳回时联动关闭售后单并回滚订单
        if refund["status"] == "已驳回":
            _change_status(after_sale_id, "已关闭", "自动关闭", "auto",
                           f"退款被风控自动驳回（{refund['rule_hit']}）")
            order_svc.restore_order(order["order_id"], "auto", "退款自动驳回")
            message = "审核通过，但退款被风控自动驳回，售后单已关闭"
        else:
            message = f"审核通过，退款单 {refund['refund_id']} 已创建（{refund['status']}）"
    elif row["type"] == "投诉":
        _change_status(after_sale_id, "审核通过", "审核通过", operator, remark)
        message = "投诉已受理"
    else:  # 退货退款 / 换货 / 维修
        _change_status(after_sale_id, "待寄回", "审核通过", operator,
                       f"{remark}，请顾客寄回商品")
        message = "审核通过，等待顾客寄回商品"

    helpers.log_op(principal, "售后审核通过", f"after_sales:{after_sale_id}", message)
    return ok(_detail(_get_after_sale(after_sale_id)), message)


@router.post("/{after_sale_id}/reject")
def reject(after_sale_id: str, body: ReviewRequest | None = None,
           principal: dict = Depends(auth.require_scope("aftersales:write"))):
    """审核驳回：售后单关闭，订单回滚到申请前状态。"""
    row = _get_after_sale(after_sale_id)
    if row["status"] != "待审核":
        raise AppError(
            f"售后单当前状态为「{row['status']}」，不允许驳回", "INVALID_STATUS_TRANSITION", 409)
    remark = (body.remark if body else "") or "不符合售后条件，审核驳回"
    _change_status(after_sale_id, "审核驳回", "审核驳回", principal["username"], remark)
    order_svc.restore_order(row["order_id"], principal["username"], "售后驳回", remark)
    helpers.log_op(principal, "售后驳回", f"after_sales:{after_sale_id}", remark)
    return ok(_detail(_get_after_sale(after_sale_id)), "售后申请已驳回")


@router.post("/{after_sale_id}/receive")
def receive(after_sale_id: str,
            principal: dict = Depends(auth.require_scope("aftersales:write"))):
    """商家确认收到寄回商品：待寄回/已寄回 → 商家收货。"""
    row = _get_after_sale(after_sale_id)
    to_status = rules.next_after_sale_status("receive", row["status"])
    _change_status(after_sale_id, to_status, "商家收货", principal["username"],
                   "已收到顾客寄回的商品")
    helpers.log_op(principal, "售后收货", f"after_sales:{after_sale_id}", "商家已收货")
    return ok(_detail(_get_after_sale(after_sale_id)), "商家已确认收货")


@router.post("/{after_sale_id}/complete")
def complete(after_sale_id: str, body: ReviewRequest | None = None,
             principal: dict = Depends(auth.require_scope("aftersales:write"))):
    """完成售后：
    - 退货退款（商家收货后）：自动创建退款单（人工审核通过）等待打款
    - 换货/维修（商家收货后）：订单回滚到申请前状态
    - 投诉（审核通过后）：直接完成
    - 仅退款（退款中）：要求关联退款已打款
    """
    row = _get_after_sale(after_sale_id)
    to_status = rules.next_after_sale_status("complete", row["status"])
    operator = principal["username"]
    order = order_svc.get_order(row["order_id"])

    if row["type"] == "退货退款" and row["status"] == "商家收货":
        # 创建退款单并直接审核通过（商家已收到退货）
        refund = refund_routes.create_refund_with_review(
            order, row["reason"], row["amount"] or order["total"], after_sale_id, operator)
        db.execute(
            """UPDATE refunds SET status = '审核通过', review_type = 'manual',
               review_result = '人工审核通过（退货已签收）', updated_at = ?
               WHERE refund_id = ?""",
            (rules.now_str(), refund["refund_id"]),
        )
        db.execute(
            "UPDATE after_sales SET refund_id = ?, updated_at = ? WHERE after_sale_id = ?",
            (refund["refund_id"], rules.now_str(), after_sale_id),
        )
        _change_status(after_sale_id, to_status, "完成售后", operator,
                       "退货已收货，退款单已创建等待打款")
        message = f"售后已完成，退款单 {refund['refund_id']} 等待打款"
    elif row["type"] == "仅退款" and row["status"] == "退款中":
        refund = db.query_one(
            "SELECT * FROM refunds WHERE refund_id = ?", (row["refund_id"],)
        ) if row["refund_id"] else None
        if not refund or refund["status"] != "已退款":
            raise AppError("关联退款单尚未打款完成，无法结案", "REFUND_PENDING", 409)
        _change_status(after_sale_id, to_status, "完成售后", operator, "退款已到账，售后结案")
        message = "售后已完成"
    elif row["type"] in ("换货", "维修") and row["status"] == "商家收货":
        _change_status(after_sale_id, to_status, "完成售后", operator, "换货/维修已完成")
        order_svc.restore_order(row["order_id"], operator, "售后完成",
                                f"{row['type']}完成，订单恢复")
        message = "售后已完成，订单状态已恢复"
    else:
        remark = (body.remark if body else "") or "售后处理完成"
        _change_status(after_sale_id, to_status, "完成售后", operator, remark)
        message = "售后已完成"

    helpers.log_op(principal, "完成售后", f"after_sales:{after_sale_id}", message)
    return ok(_detail(_get_after_sale(after_sale_id)), message)
