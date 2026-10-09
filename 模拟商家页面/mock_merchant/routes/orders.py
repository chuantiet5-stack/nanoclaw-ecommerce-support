"""订单管理 API：列表 / 详情 / 创建 / 支付 / 取消 / 发货 / 确认收货 / 完成。

状态机（禁止非法跳转，409 拦截）：
  待付款 --pay--> 已付款（自动备货成功则进入 待发货）
  待付款/已付款/待发货 --cancel--> 已取消
  已付款/待发货 --ship--> 已发货（扣减锁定库存并创建物流单）
  已发货/运输中 --confirm--> 已签收
  已签收 --complete--> 已完成
  售后/退款联动：start_refund → 退款中；start_after_sale → 售后中
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from mock_merchant import auth, db, rules
from mock_merchant import order_service as order_svc
from mock_merchant.faults import is_flag_on
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers
from mock_merchant.routes import inventory as inv

router = APIRouter(prefix="/orders", tags=["订单管理"])


class OrderItemIn(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class OrderCreate(BaseModel):
    customer_id: str
    items: list[OrderItemIn] = Field(min_length=1)
    receiver_name: str
    receiver_phone: str
    address: str
    discount: float = Field(default=0, ge=0)
    freight: float = Field(default=0, ge=0)


class CancelRequest(BaseModel):
    reason: str = "顾客主动取消"


class ShipRequest(BaseModel):
    carrier: str = "顺丰"  # 顺丰/中通/圆通/韵达/申通


def _check_access(principal: dict, order: dict) -> None:
    """顾客 Token 只能操作/查询本人的订单。"""
    if principal["type"] == "customer" and principal["subject_id"] != order["customer_id"]:
        raise AppError("无权访问该订单", "ORDER_ACCESS_DENIED", 403)


def _detail(order_row: dict) -> dict:
    data = helpers.order_dict(order_row)
    data["logs"] = order_svc.order_logs(order_row["order_id"])
    logistics = db.query_one(
        "SELECT * FROM logistics WHERE order_id = ?", (order_row["order_id"],)
    )
    data["logistics"] = helpers.logistics_dict(logistics) if logistics else None
    return data


@router.get("")
def list_orders(
    status: str = Query(default="", description="按订单状态筛选"),
    customer_id: str = Query(default="", description="按顾客筛选"),
    keyword: str = Query(default="", description="按订单号/收货人模糊搜索"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("orders:read")),
):
    """订单列表。顾客 Token 自动只返回本人订单（数据隔离）。"""
    if principal["type"] == "customer":
        customer_id = principal["subject_id"]
    where, params = ["1=1"], []
    if status:
        where.append("status = ?")
        params.append(status)
    if customer_id:
        where.append("customer_id = ?")
        params.append(customer_id)
    if keyword:
        where.append("(order_id LIKE ? OR receiver_name LIKE ?)")
        params += [f"%{keyword}%"] * 2
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM orders WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM orders WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.order_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/{order_id}")
def get_order(order_id: str,
              principal: dict = Depends(auth.require_scope("orders:read"))):
    """订单详情（含商品明细、脱敏手机号、操作日志与物流信息）。"""
    row = order_svc.get_order(order_id)
    _check_access(principal, row)
    return ok(_detail(row))


@router.post("")
def create_order(body: OrderCreate,
                 principal: dict = Depends(auth.require_scope("orders:read"))):
    """创建订单（待付款）。顾客 Token 仅能为本人下单。"""
    if principal["type"] == "customer" and principal["subject_id"] != body.customer_id:
        raise AppError("无权为其他顾客下单", "FORBIDDEN", 403)
    customer = db.query_one(
        "SELECT * FROM customers WHERE customer_id = ?", (body.customer_id,)
    )
    if not customer:
        raise AppError(f"顾客 {body.customer_id} 不存在", "CUSTOMER_NOT_FOUND", 404)

    items, goods_amount = [], 0.0
    for it in body.items:
        product = db.query_one("SELECT * FROM products WHERE sku = ?", (it.sku,))
        if not product:
            raise AppError(f"商品 {it.sku} 不存在", "PRODUCT_NOT_FOUND", 404)
        if product["status"] != "在售":
            raise AppError(f"商品 {it.sku} 已下架", "PRODUCT_OFF_SHELF", 409)
        available = product["stock"] - product["locked_stock"]
        if available < it.qty:
            raise AppError(
                f"商品 {it.sku} 可售库存不足（剩余 {available}，需要 {it.qty}）",
                "INSUFFICIENT_STOCK", 409,
            )
        amount = round(it.qty * product["price"], 2)
        goods_amount = round(goods_amount + amount, 2)
        items.append({"sku": it.sku, "name": product["name"], "qty": it.qty,
                      "price": product["price"], "amount": amount})

    total = round(goods_amount - body.discount + body.freight, 2)
    if total < 0:
        raise AppError("优惠金额不能超过商品总额", "INVALID_AMOUNT", 400)
    order_id = rules.new_order_id()
    now = rules.now_str()
    db.execute(
        """INSERT INTO orders (order_id, customer_id, status, items, goods_amount,
           discount, freight, total, receiver_name, receiver_phone, address,
           stock_locked, created_at, updated_at)
           VALUES (?,?, '待付款', ?,?,?,?,?,?,?,?,0,?,?)""",
        (order_id, body.customer_id, db.dumps(items), goods_amount, body.discount,
         body.freight, total, body.receiver_name, body.receiver_phone, body.address,
         now, now),
    )
    order_svc.log_order(order_id, "创建订单", "", "待付款", body.customer_id, "顾客提交订单")
    helpers.log_op(principal, "创建订单", f"orders:{order_id}",
                   f"为顾客 {body.customer_id} 创建订单，金额 ¥{total}")
    return ok(_detail(order_svc.get_order(order_id)), "订单创建成功", 201)


@router.post("/{order_id}/pay")
def pay_order(order_id: str,
              principal: dict = Depends(auth.require_scope("orders:read"))):
    """支付订单：待付款 → 已付款；库存充足自动备货进入「待发货」。"""
    if is_flag_on("duplicate_request"):
        raise AppError("模拟故障：重复请求被拦截", "DUPLICATE_REQUEST", 409)
    if is_flag_on("inventory_shortage"):
        raise AppError("模拟故障：库存不足，支付备货失败", "INSUFFICIENT_STOCK", 409)
    row = order_svc.get_order(order_id)
    _check_access(principal, row)
    to_status = rules.next_order_status("pay", row["status"])  # 待付款 → 已付款
    operator = principal["username"]

    # 已付款（瞬时状态，随后尝试备货）
    order_svc.change_status(order_id, "已付款", "支付订单", operator,
                            "支付成功", extra_set="paid_at = ?",
                            extra_params=(rules.now_str(),))

    # 自动备货：锁定库存，成功则进入待发货
    items = db.loads(row["items"], [])
    locked_ok = all(
        inv.try_lock(i["sku"], i["qty"], order_id, operator, "支付锁库存")
        for i in items
    )
    if locked_ok:
        order_svc.change_status(order_id, "待发货", "自动备货", operator,
                                "库存已锁定，等待商家发货")
        db.execute("UPDATE orders SET stock_locked = 1 WHERE order_id = ?", (order_id,))
        message = "支付成功，已进入待发货"
    else:
        # 备货失败回滚已锁定的部分
        for i in items:
            inv.release_lock(i["sku"], i["qty"], order_id, operator, "备货失败回滚")
        order_svc.log_order(order_id, "备货失败", "已付款", "已付款", operator,
                            "库存不足，订单滞留在已付款状态等待补货")
        message = "支付成功，但库存不足，订单等待补货（已付款）"

    helpers.log_op(principal, "支付订单", f"orders:{order_id}", message)
    return ok(_detail(order_svc.get_order(order_id)), message)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: str, body: CancelRequest,
                 principal: dict = Depends(auth.require_scope("orders:read"))):
    """取消订单：待付款/已付款/待发货 → 已取消；已支付的自动原路退款。"""
    if is_flag_on("duplicate_request"):
        raise AppError("模拟故障：重复请求被拦截", "DUPLICATE_REQUEST", 409)
    row = order_svc.get_order(order_id)
    _check_access(principal, row)
    to_status = rules.next_order_status("cancel", row["status"])
    operator = principal["username"]

    # 释放锁定的库存
    if row["stock_locked"]:
        for i in db.loads(row["items"], []):
            inv.release_lock(i["sku"], i["qty"], order_id, operator, "取消订单释放库存")
        db.execute("UPDATE orders SET stock_locked = 0 WHERE order_id = ?", (order_id,))

    order_svc.change_status(order_id, to_status, "取消订单", operator, body.reason,
                            extra_set="cancel_reason = ?",
                            extra_params=(body.reason,))

    # 已支付订单自动原路退款（模拟即时退款通道）
    if row["paid_at"]:
        from mock_merchant.routes import refunds as refund_routes
        refund_routes.mark_refunded_for_cancel(row, operator)

    helpers.log_op(principal, "取消订单", f"orders:{order_id}", body.reason)
    return ok(_detail(order_svc.get_order(order_id)), "订单已取消")


@router.post("/{order_id}/ship")
def ship_order(order_id: str, body: ShipRequest,
               principal: dict = Depends(auth.require_scope("orders:write"))):
    """商家发货：已付款/待发货 → 已发货；扣减库存并创建物流单。"""
    if is_flag_on("inventory_shortage"):
        raise AppError("模拟故障：库存不足，发货失败", "INSUFFICIENT_STOCK", 409)
    row = order_svc.get_order(order_id)
    to_status = rules.next_order_status("ship", row["status"])
    operator = principal["username"]
    if body.carrier not in rules.CARRIERS:
        raise AppError(f"快递公司仅支持：{'、'.join(rules.CARRIERS)}", "INVALID_CARRIER", 400)

    items = db.loads(row["items"], [])
    if row["stock_locked"]:
        # 扣减已锁定库存
        for i in items:
            if not inv.deduct_locked(i["sku"], i["qty"], order_id, operator):
                raise AppError(f"商品 {i['sku']} 库存扣减失败（库存不足）",
                               "INSUFFICIENT_STOCK", 409)
        db.execute("UPDATE orders SET stock_locked = 0 WHERE order_id = ?", (order_id,))
    else:
        # 已付款但未锁定（历史备货失败）：发货前需有可售库存
        for i in items:
            product = db.query_one(
                "SELECT stock, locked_stock FROM products WHERE sku = ?", (i["sku"],)
            )
            available = (product["stock"] - product["locked_stock"]) if product else 0
            if available < i["qty"]:
                raise AppError(
                    f"商品 {i['sku']} 可售库存不足（剩余 {available}，需要 {i['qty']}），无法发货",
                    "INSUFFICIENT_STOCK", 409)
            db.execute(
                "UPDATE products SET stock = stock - ?, updated_at = ? WHERE sku = ?",
                (i["qty"], rules.now_str(), i["sku"]),
            )

    # 创建物流单
    from mock_merchant.routes import logistics as logistics_routes
    logistics_routes.create_logistics_record(order_id, body.carrier, operator)

    order_svc.change_status(order_id, to_status, "商家发货", operator,
                            f"已通过{body.carrier}发货", extra_set="shipped_at = ?",
                            extra_params=(rules.now_str(),))
    helpers.log_op(principal, "商家发货", f"orders:{order_id}",
                   f"{order_id} 通过{body.carrier}发货")
    return ok(_detail(order_svc.get_order(order_id)), "订单已发货")


@router.post("/{order_id}/confirm")
def confirm_order(order_id: str,
                  principal: dict = Depends(auth.require_scope("orders:read"))):
    """确认收货：已发货/运输中 → 已签收。"""
    row = order_svc.get_order(order_id)
    _check_access(principal, row)
    to_status = rules.next_order_status("confirm", row["status"])
    operator = principal["username"]

    # 物流同步签收
    logistics = db.query_one("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
    if logistics and logistics["status"] != "已签收":
        from mock_merchant.routes import logistics as logistics_routes
        logistics_routes.sign_logistics(order_id, operator, "顾客确认收货")

    order_svc.change_status(order_id, to_status, "确认收货", operator, "顾客确认收货",
                            extra_set="signed_at = ?",
                            extra_params=(rules.now_str(),))
    helpers.log_op(principal, "确认收货", f"orders:{order_id}", "订单已签收")
    return ok(_detail(order_svc.get_order(order_id)), "订单已签收")


@router.post("/{order_id}/complete")
def complete_order(order_id: str,
                   principal: dict = Depends(auth.require_scope("orders:write"))):
    """交易完成：已签收 → 已完成。"""
    row = order_svc.get_order(order_id)
    to_status = rules.next_order_status("complete", row["status"])
    order_svc.change_status(order_id, to_status, "交易完成", principal["username"],
                            "订单交易完成", extra_set="completed_at = ?",
                            extra_params=(rules.now_str(),))
    helpers.log_op(principal, "交易完成", f"orders:{order_id}", "订单交易完成")
    return ok(_detail(order_svc.get_order(order_id)), "订单已完成")
