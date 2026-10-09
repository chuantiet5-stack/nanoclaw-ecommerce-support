"""路由共享辅助：对象序列化（含脱敏）与操作审计日志。"""

from mock_merchant import db, rules
from mock_merchant.response import get_request_id


# ---------- 审计 ----------

def log_op(principal: dict, action: str, resource: str = "", detail: str = "") -> None:
    """记录操作审计日志（所有写操作都会记录）。"""
    actor = principal.get("username") or principal.get("subject_id") or "anonymous"
    db.execute(
        """INSERT INTO op_logs (actor, role, action, resource, detail, request_id, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (actor, principal.get("role", ""), action, resource, detail,
         get_request_id(), rules.now_str()),
    )


# ---------- 序列化 ----------

def product_dict(row: dict) -> dict:
    """商品对象（字段与 V4 goods 模型对齐：sku/name/category/price/highlights/
    description/stock/sizes/scenes/link，另附库存与营销扩展字段）。"""
    sizes = db.loads(row.get("sizes"), {})
    return {
        "sku": row["sku"],
        "name": row["name"],
        "category": row["category"],
        "price": row["price"],
        "original_price": row["original_price"],
        "highlights": row["highlights"],
        "description": row["description"],
        "image_url": row["image_url"],
        "sizes": sizes,
        "colors": db.loads(row.get("colors"), []),
        "scenes": db.loads(row.get("scenes"), []),
        "link": row["link"],
        "stock": row["stock"],
        "locked_stock": row["locked_stock"],
        "available_stock": max(row["stock"] - row["locked_stock"], 0),
        "safety_stock": row["safety_stock"],
        "sales": row["sales"],
        "status": row["status"],
        "low_stock_warning": row["stock"] <= row["safety_stock"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def order_dict(row: dict) -> dict:
    """订单对象（字段与 V4 orders 模型对齐：order_id/customer_id/status/items/
    total/receiver_name/receiver_phone/address；手机号脱敏返回）。"""
    return {
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "status": row["status"],
        "items": db.loads(row.get("items"), []),
        "goods_amount": row["goods_amount"],
        "discount": row["discount"],
        "freight": row["freight"],
        "total": row["total"],
        "receiver_name": row["receiver_name"],
        "receiver_phone": rules.mask_phone(row["receiver_phone"]),
        "address": row["address"],
        "created_at": row["created_at"],
        "paid_at": row["paid_at"],
        "shipped_at": row["shipped_at"],
        "signed_at": row["signed_at"],
        "completed_at": row["completed_at"],
        "cancel_reason": row["cancel_reason"],
        "updated_at": row["updated_at"],
    }


def order_log_dict(row: dict) -> dict:
    return {
        "action": row["action"],
        "from_status": row["from_status"],
        "to_status": row["to_status"],
        "operator": row["operator"],
        "remark": row["remark"],
        "created_at": row["created_at"],
    }


def logistics_dict(row: dict) -> dict:
    """物流对象（traces 结构与 V4 logistics.json 对齐：status/location/time）。"""
    return {
        "order_id": row["order_id"],
        "carrier": row["carrier"],
        "tracking_no": row["tracking_no"],
        "status": row["status"],
        "current_location": row["current_location"],
        "traces": db.loads(row.get("traces"), []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def after_sale_dict(row: dict) -> dict:
    return {
        "after_sale_id": row["after_sale_id"],
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "type": row["type"],
        "status": row["status"],
        "reason": row["reason"],
        "description": row["description"],
        "amount": row["amount"],
        "refund_id": row["refund_id"],
        "admin_remark": row["admin_remark"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def refund_dict(row: dict) -> dict:
    return {
        "refund_id": row["refund_id"],
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "after_sale_id": row["after_sale_id"],
        "amount": row["amount"],
        "reason": row["reason"],
        "review_type": row["review_type"],
        "review_result": row["review_result"],
        "risk_result": row["risk_result"],
        "rule_hit": row["rule_hit"],
        "status": row["status"],
        "refunded_at": row["refunded_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def ticket_dict(row: dict) -> dict:
    return {
        "ticket_id": row["ticket_id"],
        "customer_id": row["customer_id"],
        "order_id": row["order_id"],
        "type": row["type"],
        "status": row["status"],
        "priority": row["priority"],
        "priority_label": row["priority_label"],
        "summary": row["summary"],
        "description": row["description"],
        "assigned_to": row["assigned_to"],
        "records": db.loads(row.get("records"), []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def customer_dict(row: dict, stats: bool = True) -> dict:
    """顾客对象：手机号/地址脱敏，不返回密码与任何身份凭证字段。"""
    data = {
        "customer_id": row["customer_id"],
        "username": row["username"],
        "name": rules.mask_name(row["name"]),
        "phone": rules.mask_phone(row["phone"]),
        "address": rules.mask_address(row["address"]),
        "level": row["level"],
        "points": row["points"],
        "created_at": row["created_at"],
    }
    if stats:
        orders = db.query(
            "SELECT COUNT(*) AS n FROM orders WHERE customer_id = ?", (row["customer_id"],)
        )
        refunds = db.query(
            "SELECT COUNT(*) AS n FROM refunds WHERE customer_id = ? AND status IN ('审核通过','已退款')",
            (row["customer_id"],),
        )
        data["order_count"] = orders[0]["n"] if orders else 0
        data["refund_count"] = refunds[0]["n"] if refunds else 0
    return data
