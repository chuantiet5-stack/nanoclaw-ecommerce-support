"""顾客管理 API：列表 / 详情 / 顾客的订单、售后与工单。

数据脱敏规则：
- 手机号 / 地址 / 姓名脱敏返回
- 不返回密码、Token、身份凭证等任何敏感字段
- 顾客 Token 只能查询自己的数据
"""

from fastapi import APIRouter, Depends, Query

from mock_merchant import auth, db
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers

router = APIRouter(prefix="/customers", tags=["顾客管理"])


def _get_customer(customer_id: str) -> dict:
    row = db.query_one("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))
    if not row:
        raise AppError(f"顾客 {customer_id} 不存在", "CUSTOMER_NOT_FOUND", 404)
    return row


def _check_self(principal: dict, customer_id: str) -> None:
    """顾客只能查询自己的个人数据。"""
    if principal["type"] == "customer" and principal["subject_id"] != customer_id:
        raise AppError("无权访问其他顾客的数据", "CUSTOMER_ACCESS_DENIED", 403)


@router.get("")
def list_customers(
    keyword: str = Query(default="", description="按姓名/账号/顾客ID模糊搜索"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("customers:read")),
):
    """顾客列表（仅商家员工可查看，数据脱敏）。"""
    if principal["type"] == "customer":
        raise AppError("顾客无权查看顾客列表", "FORBIDDEN", 403)
    where, params = ["1=1"], []
    if keyword:
        where.append("(name LIKE ? OR username LIKE ? OR customer_id LIKE ?)")
        params += [f"%{keyword}%"] * 3
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM customers WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM customers WHERE {cond} ORDER BY id LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.customer_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


@router.get("/{customer_id}")
def get_customer(customer_id: str,
                 principal: dict = Depends(auth.require_scope("customers:read"))):
    """顾客详情（脱敏；顾客 Token 仅可查自己）。"""
    row = _get_customer(customer_id)
    _check_self(principal, customer_id)
    data = helpers.customer_dict(row)
    # 顾客本人可见未脱敏姓名与手机号（自己的数据）
    if principal["type"] == "customer":
        data["name"] = row["name"]
        data["phone"] = row["phone"]
        data["address"] = row["address"]
    return ok(data)


@router.get("/{customer_id}/orders")
def customer_orders(
    customer_id: str,
    status: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("customers:read")),
):
    """顾客的订单列表（归属校验：顾客仅可查自己）。"""
    _get_customer(customer_id)
    _check_self(principal, customer_id)
    where, params = ["customer_id = ?"], [customer_id]
    if status:
        where.append("status = ?")
        params.append(status)
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM orders WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM orders WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.order_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


@router.get("/{customer_id}/after-sales")
def customer_after_sales(
    customer_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("customers:read")),
):
    """顾客的售后单列表（归属校验）。"""
    _get_customer(customer_id)
    _check_self(principal, customer_id)
    total = db.query_one(
        "SELECT COUNT(*) AS n FROM after_sales WHERE customer_id = ?", (customer_id,)
    )["n"]
    rows = db.query(
        "SELECT * FROM after_sales WHERE customer_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (customer_id, page_size, (page - 1) * page_size),
    )
    return ok({
        "list": [helpers.after_sale_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


@router.get("/{customer_id}/tickets")
def customer_tickets(
    customer_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("customers:read")),
):
    """顾客的工单列表（归属校验）。"""
    _get_customer(customer_id)
    _check_self(principal, customer_id)
    total = db.query_one(
        "SELECT COUNT(*) AS n FROM tickets WHERE customer_id = ?", (customer_id,)
    )["n"]
    rows = db.query(
        "SELECT * FROM tickets WHERE customer_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (customer_id, page_size, (page - 1) * page_size),
    )
    return ok({
        "list": [helpers.ticket_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })
