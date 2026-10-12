"""退款管理 API：列表 / 详情 / 发起退款（自动风控审核）/ 审核通过 / 驳回 / 打款。

风控规则与 V4 refund_policy.py 的 R0-R8 完全对齐：
- auto_approve：小额 + 签收 7 天内 + 无理由原因 + 账号正常 → 自动通过
- auto_reject：超 7 天无理由期且无质量问题 → 自动拒绝
- manual：大额 / 质量问题 / 高风险账号 / 频次超限 → 转人工
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from mock_merchant import auth, db, rules
from mock_merchant import order_service as order_svc
from mock_merchant import refund_policy
from mock_merchant.faults import is_flag_on
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers
from mock_merchant.routes import inventory as inv

router = APIRouter(prefix="/refunds", tags=["退款管理"])


class RefundCreate(BaseModel):
    order_id: str
    customer_id: str
    amount: float = Field(default=0, ge=0, description="退款金额，默认订单实付金额")
    reason: str = ""
    after_sale_id: str = ""


class RefundReview(BaseModel):
    remark: str = ""


def create_refund_with_review(order: dict, reason: str, amount: float,
                              after_sale_id: str = "", operator: str = "system") -> dict:
    """创建退款单并执行自动风控审核（供退款路由与售后审核共用）。"""
    customer_id = order["customer_id"]
    amount = round(amount or order["total"], 2)
    refund_id = rules.new_refund_id()
    now = rules.now_str()

    # 风控决策（故障开关：模拟风控拦截）
    if is_flag_on("refund_risk_block"):
        decision = refund_policy.RefundDecision(
            "manual", "模拟风控拦截：账号风险分超限，转人工审核", "R1-SIM", None)
    else:
        decision = refund_policy.classify_refund(order, reason, customer_id)

    if decision.action == "auto_approve":
        status, review_result = "审核通过", "自动通过"
    elif decision.action == "auto_reject":
        status, review_result = "已驳回", "自动拒绝"
    else:
        status, review_result = "待审核", "转人工审核"
    review_type = rules.REFUND_REVIEW_TYPE[decision.action]

    db.execute(
        """INSERT INTO refunds (refund_id, order_id, customer_id, after_sale_id, amount,
           reason, review_type, review_result, risk_result, rule_hit, status,
           created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (refund_id, order["order_id"], customer_id, after_sale_id, amount, reason,
         review_type, review_result, decision.reason, decision.rule, status, now, now),
    )
    db.execute(
        """INSERT INTO refund_logs (refund_id, action, from_status, to_status, operator,
           remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (refund_id, "创建退款单", "", "待审核", operator,
         f"{reason}（{decision.rule}：{decision.reason}）", now),
    )
    if status == "审核通过":
        db.execute(
            """INSERT INTO refund_logs (refund_id, action, from_status, to_status,
               operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
            (refund_id, "自动审核", "待审核", "审核通过", "auto", decision.reason, now),
        )
    elif status == "已驳回":
        db.execute(
            """INSERT INTO refund_logs (refund_id, action, from_status, to_status,
               operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
            (refund_id, "自动驳回", "待审核", "已驳回", "auto", decision.reason, now),
        )

    # 订单联动：进入退款中（自动驳回则保持原状态）
    if status != "已驳回":
        order_svc.start_refund(order["order_id"], operator,
                               f"退款单 {refund_id}（{reason}）")
    else:
        order_svc.log_order(order["order_id"], "退款被自动驳回",
                            order["status"], order["status"], "auto", decision.reason)

    return db.query_one("SELECT * FROM refunds WHERE refund_id = ?", (refund_id,))


def mark_refunded_for_cancel(order: dict, operator: str) -> None:
    """已支付订单取消时自动原路退款（模拟即时退款通道，直接完成）。"""
    refund = create_refund_with_review(
        order, "订单取消，自动原路退款", order["total"], "", operator)
    db.execute(
        """UPDATE refunds SET status = '已退款', refunded_at = ?, updated_at = ?,
           review_type = 'auto', review_result = '取消订单自动退款' WHERE refund_id = ?""",
        (rules.now_str(), rules.now_str(), refund["refund_id"]),
    )
    db.execute(
        """INSERT INTO refund_logs (refund_id, action, from_status, to_status, operator,
           remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (refund["refund_id"], "退款成功", "审核通过", "已退款", "system",
         "取消订单，退款原路退回", rules.now_str()),
    )
    order_svc.change_status(order["order_id"], "已取消", "取消订单退款", operator,
                            "已支付订单取消，退款原路退回")


def _get_refund(refund_id: str) -> dict:
    row = db.query_one("SELECT * FROM refunds WHERE refund_id = ?", (refund_id,))
    if not row:
        raise AppError(f"退款单 {refund_id} 不存在", "REFUND_NOT_FOUND", 404)
    return row


def _check_access(principal: dict, refund: dict) -> None:
    if principal["type"] == "customer" and principal["subject_id"] != refund["customer_id"]:
        raise AppError("无权访问该退款单", "REFUND_ACCESS_DENIED", 403)


def _detail(refund: dict) -> dict:
    data = helpers.refund_dict(refund)
    logs = db.query(
        "SELECT * FROM refund_logs WHERE refund_id = ? ORDER BY id ASC",
        (refund["refund_id"],),
    )
    data["logs"] = [
        {"action": l["action"], "from_status": l["from_status"],
         "to_status": l["to_status"], "operator": l["operator"],
         "remark": l["remark"], "created_at": l["created_at"]}
        for l in logs
    ]
    return data


@router.get("")
def list_refunds(
    status: str = Query(default=""),
    customer_id: str = Query(default=""),
    order_id: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("refunds:read")),
):
    """退款单列表。顾客 Token 自动只返回本人退款单。"""
    if principal["type"] == "customer":
        customer_id = principal["subject_id"]
    where, params = ["1=1"], []
    if status:
        where.append("status = ?")
        params.append(status)
    if customer_id:
        where.append("customer_id = ?")
        params.append(customer_id)
    if order_id:
        where.append("order_id = ?")
        params.append(order_id)
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM refunds WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM refunds WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.refund_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


@router.get("/{refund_id}")
def get_refund(refund_id: str,
               principal: dict = Depends(auth.require_scope("refunds:read"))):
    """退款单详情（含风控结果、审核记录、操作日志）。"""
    refund = _get_refund(refund_id)
    _check_access(principal, refund)
    return ok(_detail(refund))


@router.post("")
def create_refund(body: RefundCreate,
                  principal: dict = Depends(auth.require_scope("refunds:create"))):
    """发起退款（自动执行风控审核：自动通过 / 自动驳回 / 转人工）。"""
    if is_flag_on("duplicate_request"):
        raise AppError("模拟故障：重复请求被拦截", "DUPLICATE_REQUEST", 409)
    order = order_svc.get_order(body.order_id)
    if order["customer_id"] != body.customer_id:
        raise AppError("订单不属于该顾客，无法发起退款", "ORDER_OWNERSHIP_MISMATCH", 403)
    if principal["type"] == "customer" and principal["subject_id"] != body.customer_id:
        raise AppError("无权为其他顾客发起退款", "FORBIDDEN", 403)
    if order["status"] in ("待付款",):
        raise AppError("订单尚未支付，无需退款，请直接取消订单",
                       "INVALID_STATUS_TRANSITION", 409)
    if order["status"] in ("已取消", "已退款"):
        raise AppError(f"订单当前状态为「{order['status']}」，不能发起退款",
                       "INVALID_STATUS_TRANSITION", 409)
    # 同一订单已有进行中的退款
    pending = db.query_one(
        "SELECT 1 AS x FROM refunds WHERE order_id = ? AND status IN ('待审核','审核通过')",
        (body.order_id,),
    )
    if pending:
        raise AppError("该订单已有进行中的退款单，请勿重复申请", "REFUND_DUPLICATED", 409)

    amount = round(body.amount or order["total"], 2)
    if amount <= 0 or amount > order["total"]:
        raise AppError(f"退款金额无效（应大于 0 且不超过实付 ¥{order['total']}）",
                       "INVALID_AMOUNT", 400)

    refund = create_refund_with_review(
        order, body.reason or "未填写原因", amount, body.after_sale_id,
        principal["username"])
    helpers.log_op(principal, "发起退款", f"refunds:{refund['refund_id']}",
                   f"订单 {body.order_id} 退款 ¥{amount}（{refund['status']}）")
    data = _detail(refund)
    data["risk_decision"] = {
        "rule": refund["rule_hit"], "risk_result": refund["risk_result"],
    }
    return ok(data, f"退款单已创建（{refund['status']}）", 201)


@router.post("/{refund_id}/approve")
def approve(refund_id: str, body: RefundReview | None = None,
            principal: dict = Depends(auth.require_scope("refunds:write"))):
    """人工审核通过：待审核 → 审核通过。"""
    refund = _get_refund(refund_id)
    to_status = rules.next_refund_status("approve", refund["status"])
    now = rules.now_str()
    db.execute(
        "UPDATE refunds SET status = ?, review_result = '人工审核通过', updated_at = ? "
        "WHERE refund_id = ?",
        (to_status, now, refund_id),
    )
    db.execute(
        """INSERT INTO refund_logs (refund_id, action, from_status, to_status, operator,
           remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (refund_id, "人工审核", refund["status"], to_status, principal["username"],
         (body.remark if body else "") or "人工审核通过", now),
    )
    helpers.log_op(principal, "退款审核通过", f"refunds:{refund_id}",
                   (body.remark if body else "") or "人工审核通过")
    return ok(_detail(_get_refund(refund_id)), "退款已审核通过")


@router.post("/{refund_id}/reject")
def reject(refund_id: str, body: RefundReview | None = None,
           principal: dict = Depends(auth.require_scope("refunds:write"))):
    """人工驳回：待审核 → 已驳回；订单回滚到退款前状态。"""
    refund = _get_refund(refund_id)
    to_status = rules.next_refund_status("reject", refund["status"])
    now = rules.now_str()
    remark = (body.remark if body else "") or "人工审核驳回"
    db.execute(
        "UPDATE refunds SET status = ?, review_result = '人工审核驳回', updated_at = ? "
        "WHERE refund_id = ?",
        (to_status, now, refund_id),
    )
    db.execute(
        """INSERT INTO refund_logs (refund_id, action, from_status, to_status, operator,
           remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (refund_id, "人工驳回", refund["status"], to_status, principal["username"],
         remark, now),
    )
    # 订单回滚（若因该退款进入退款中）
    order_svc.restore_order(refund["order_id"], principal["username"],
                            "退款驳回", remark)
    # 同步关闭关联售后单（如有）
    if refund["after_sale_id"]:
        db.execute(
            "UPDATE after_sales SET status = '已关闭', updated_at = ? WHERE after_sale_id = ?",
            (now, refund["after_sale_id"]),
        )
    helpers.log_op(principal, "退款驳回", f"refunds:{refund_id}", remark)
    return ok(_detail(_get_refund(refund_id)), "退款已驳回")


@router.post("/{refund_id}/success")
def mark_success(refund_id: str, body: RefundReview | None = None,
                 principal: dict = Depends(auth.require_scope("refunds:write"))):
    """退款打款成功：审核通过 → 已退款；订单变为已退款并处理库存。"""
    refund = _get_refund(refund_id)
    to_status = rules.next_refund_status("success", refund["status"])
    now = rules.now_str()
    order = order_svc.get_order(refund["order_id"])
    operator = principal["username"]

    db.execute(
        "UPDATE refunds SET status = ?, refunded_at = ?, updated_at = ? WHERE refund_id = ?",
        (to_status, now, now, refund_id),
    )
    db.execute(
        """INSERT INTO refund_logs (refund_id, action, from_status, to_status, operator,
           remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (refund_id, "退款成功", refund["status"], to_status, operator,
         "退款已原路退回", now),
    )

    # 订单 → 已退款（取消订单场景保持已取消）
    if order["status"] != "已取消":
        order_svc.change_status(order["order_id"], "已退款", "退款成功", operator,
                                f"退款单 {refund_id} 已原路退回")

    # 库存处理：未发货 → 释放锁定；退货退款/换货 → 退货入库
    as_row = db.query_one(
        "SELECT * FROM after_sales WHERE after_sale_id = ?",
        (refund["after_sale_id"],),
    ) if refund["after_sale_id"] else None
    for item in db.loads(order["items"], []):
        if not order["shipped_at"]:
            if order["stock_locked"]:
                inv.release_lock(item["sku"], item["qty"], order["order_id"],
                                 operator, "退款释放库存")
        elif as_row and as_row["type"] in ("退货退款", "换货"):
            inv.restock(item["sku"], item["qty"], order["order_id"], operator)

    helpers.log_op(principal, "退款打款", f"refunds:{refund_id}",
                   f"订单 {refund['order_id']} 退款 ¥{refund['amount']}")
    return ok(_detail(_get_refund(refund_id)), "退款已原路退回")
