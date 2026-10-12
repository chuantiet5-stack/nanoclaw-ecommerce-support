"""库存管理 API：查询 / 调整 / 锁定 / 释放（含超卖风险提示）。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from mock_merchant import auth, db, rules
from mock_merchant.faults import is_flag_on
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers

router = APIRouter(prefix="/inventory", tags=["库存管理"])


class AdjustRequest(BaseModel):
    sku: str
    change_qty: int  # 正数入库 / 负数出库
    reason: str = ""


class ReserveRequest(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    order_id: str = ""
    reason: str = "下单锁库存"


class ReleaseRequest(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    order_id: str = ""
    reason: str = "释放锁定库存"


def _get_product(sku: str) -> dict:
    row = db.query_one("SELECT * FROM products WHERE sku = ?", (sku,))
    if not row:
        raise AppError(f"商品 {sku} 不存在", "PRODUCT_NOT_FOUND", 404)
    return row


def _inv_log(sku: str, action: str, qty: int, before: int, after: int,
             reason: str, operator: str) -> None:
    db.execute(
        """INSERT INTO inventory_logs (sku, action, change_qty, before_stock, after_stock,
           reason, operator, created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (sku, action, qty, before, after, reason, operator, rules.now_str()),
    )


def try_lock(sku: str, qty: int, order_id: str, operator: str, reason: str) -> bool:
    """锁定库存（可售库存不足时返回 False，不抛异常）。"""
    row = db.query_one(
        "SELECT stock, locked_stock FROM products WHERE sku = ?", (sku,)
    )
    if not row or row["stock"] - row["locked_stock"] < qty:
        return False
    db.execute(
        "UPDATE products SET locked_stock = locked_stock + ?, updated_at = ? WHERE sku = ?",
        (qty, rules.now_str(), sku),
    )
    _inv_log(sku, "reserve", qty, row["stock"], row["stock"],
             f"{reason}（订单 {order_id}）" if order_id else reason, operator)
    return True


def release_lock(sku: str, qty: int, order_id: str, operator: str, reason: str) -> bool:
    """释放锁定库存。"""
    row = db.query_one(
        "SELECT stock, locked_stock FROM products WHERE sku = ?", (sku,)
    )
    if not row or row["locked_stock"] < qty:
        return False
    db.execute(
        "UPDATE products SET locked_stock = MAX(locked_stock - ?, 0), updated_at = ? WHERE sku = ?",
        (qty, rules.now_str(), sku),
    )
    _inv_log(sku, "release", -qty, row["stock"], row["stock"],
             f"{reason}（订单 {order_id}）" if order_id else reason, operator)
    return True


def deduct_locked(sku: str, qty: int, order_id: str, operator: str) -> bool:
    """扣减已锁定的库存（发货出库）。"""
    row = db.query_one(
        "SELECT stock, locked_stock FROM products WHERE sku = ?", (sku,)
    )
    if not row or row["locked_stock"] < qty or row["stock"] < qty:
        return False
    db.execute(
        """UPDATE products SET stock = stock - ?, locked_stock = locked_stock - ?,
           updated_at = ? WHERE sku = ?""",
        (qty, qty, rules.now_str(), sku),
    )
    _inv_log(sku, "sale", -qty, row["stock"], row["stock"] - qty,
             f"发货出库（订单 {order_id}）", operator)
    return True


def restock(sku: str, qty: int, order_id: str, operator: str, reason: str = "退货入库") -> None:
    """退货入库。"""
    row = db.query_one("SELECT stock FROM products WHERE sku = ?", (sku,))
    if not row:
        return
    db.execute(
        "UPDATE products SET stock = stock + ?, updated_at = ? WHERE sku = ?",
        (qty, rules.now_str(), sku),
    )
    _inv_log(sku, "restock", qty, row["stock"], row["stock"] + qty,
             f"{reason}（订单 {order_id}）", operator)


@router.get("")
def list_inventory(
    keyword: str = Query(default=""),
    low_stock_only: bool = Query(default=False, description="仅看低于安全库存的商品"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("inventory:read")),
):
    """库存列表：当前库存 / 可售库存 / 锁定库存 / 安全库存 / 预警。"""
    where, params = ["1=1"], []
    if keyword:
        where.append("(sku LIKE ? OR name LIKE ?)")
        params += [f"%{keyword}%"] * 2
    if low_stock_only:
        where.append("stock <= safety_stock")
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM products WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM products WHERE {cond} ORDER BY stock * 1.0 / (safety_stock + 1) LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.product_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/{sku}")
def get_inventory(sku: str,
                  principal: dict = Depends(auth.require_scope("inventory:read"))):
    """单个商品库存详情（含最近调整记录）。"""
    row = _get_product(sku)
    logs = db.query(
        "SELECT * FROM inventory_logs WHERE sku = ? ORDER BY id DESC LIMIT 10", (sku,)
    )
    data = helpers.product_dict(row)
    data["recent_logs"] = [
        {"action": l["action"], "change_qty": l["change_qty"],
         "before_stock": l["before_stock"], "after_stock": l["after_stock"],
         "reason": l["reason"], "operator": l["operator"], "created_at": l["created_at"]}
        for l in logs
    ]
    return ok(data)


@router.post("/adjust")
def adjust(body: AdjustRequest,
           principal: dict = Depends(auth.require_scope("inventory:write"))):
    """库存调整（正数入库 / 负数出库；出库不得低于已锁定量）。"""
    if is_flag_on("inventory_shortage"):
        raise AppError("模拟故障：库存不足，调整失败", "INSUFFICIENT_STOCK", 409)
    row = _get_product(body.sku)
    new_stock = row["stock"] + body.change_qty
    if new_stock < 0:
        raise AppError(f"调整失败：库存不足（当前 {row['stock']}，调整 {body.change_qty}）",
                       "INSUFFICIENT_STOCK", 409)
    if new_stock < row["locked_stock"]:
        raise AppError(
            f"调整失败：调整后库存（{new_stock}）低于已锁定库存（{row['locked_stock']}）",
            "INSUFFICIENT_STOCK", 409)
    db.execute(
        "UPDATE products SET stock = ?, updated_at = ? WHERE sku = ?",
        (new_stock, rules.now_str(), body.sku),
    )
    _inv_log(body.sku, "adjust", body.change_qty, row["stock"], new_stock,
             body.reason or "人工调整", principal["username"])
    helpers.log_op(principal, "库存调整", f"inventory:{body.sku}",
                   f"{body.sku} 库存 {row['stock']} → {new_stock}（{body.reason}）")
    return ok(helpers.product_dict(_get_product(body.sku)), "库存调整成功")


@router.post("/reserve")
def reserve(body: ReserveRequest,
            principal: dict = Depends(auth.require_scope("inventory:write"))):
    """锁定库存（可售不足时返回 409，用于演示超卖风险拦截）。"""
    if is_flag_on("inventory_shortage"):
        raise AppError("模拟故障：库存不足，锁定失败", "INSUFFICIENT_STOCK", 409)
    row = _get_product(body.sku)
    available = row["stock"] - row["locked_stock"]
    if available < body.qty:
        raise AppError(
            f"可售库存不足：{body.sku} 当前可售 {available}，需要 {body.qty}",
            "INSUFFICIENT_STOCK", 409,
        )
    if not try_lock(body.sku, body.qty, body.order_id, principal["username"], body.reason):
        raise AppError("可售库存不足，锁定失败", "INSUFFICIENT_STOCK", 409)
    helpers.log_op(principal, "锁定库存", f"inventory:{body.sku}",
                   f"{body.sku} 锁定 {body.qty} 件")
    return ok(helpers.product_dict(_get_product(body.sku)), "库存锁定成功")


@router.post("/release")
def release(body: ReleaseRequest,
            principal: dict = Depends(auth.require_scope("inventory:write"))):
    """释放锁定库存。"""
    row = _get_product(body.sku)
    if row["locked_stock"] < body.qty:
        raise AppError(
            f"锁定库存不足：{body.sku} 当前锁定 {row['locked_stock']}，无法释放 {body.qty}",
            "INVALID_RELEASE", 409,
        )
    if not release_lock(body.sku, body.qty, body.order_id, principal["username"], body.reason):
        raise AppError("释放锁定库存失败", "INVALID_RELEASE", 409)
    helpers.log_op(principal, "释放库存", f"inventory:{body.sku}",
                   f"{body.sku} 释放 {body.qty} 件")
    return ok(helpers.product_dict(_get_product(body.sku)), "库存释放成功")
