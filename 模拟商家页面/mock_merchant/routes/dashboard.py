"""商家经营数据看板 API：总览 / 销售 / 订单 / 售后 / 客服。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from mock_merchant import auth, db
from mock_merchant.response import ok

router = APIRouter(prefix="/dashboard", tags=["经营看板"])


def _count(sql: str, params: tuple = ()) -> int:
    row = db.query_one(sql, params)
    return row["n"] if row else 0


@router.get("/summary")
def summary(principal: dict = Depends(auth.require_scope("dashboard:read"))):
    """经营总览：今日订单 / 销售额 / 待发货 / 售后 / 工单 / 退款金额 / 库存预警。"""
    today = datetime.now().strftime("%Y-%m-%d")
    today_orders = _count(
        "SELECT COUNT(*) AS n FROM orders WHERE created_at LIKE ?", (f"{today}%",)
    )
    row = db.query_one(
        "SELECT COALESCE(SUM(total), 0) AS s FROM orders WHERE created_at LIKE ? "
        "AND status != '待付款' AND status != '已取消'",
        (f"{today}%",),
    )
    today_sales = round(row["s"], 2) if row else 0
    pending_shipment = _count(
        "SELECT COUNT(*) AS n FROM orders WHERE status IN ('已付款','待发货')"
    )
    after_sale_orders = _count(
        "SELECT COUNT(*) AS n FROM orders WHERE status IN ('售后中','退款中')"
    )
    pending_tickets = _count(
        "SELECT COUNT(*) AS n FROM tickets WHERE status IN ('待处理','处理中')"
    )
    refund_row = db.query_one(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM refunds WHERE status = '已退款'"
    )
    low_stock = _count(
        "SELECT COUNT(*) AS n FROM products WHERE stock <= safety_stock"
    )
    return ok({
        "today_order_count": today_orders,
        "today_sales_amount": today_sales,
        "pending_shipment_count": pending_shipment,
        "after_sale_order_count": after_sale_orders,
        "pending_ticket_count": pending_tickets,
        "total_refund_amount": round(refund_row["s"], 2) if refund_row else 0,
        "low_stock_product_count": low_stock,
        "in_stock_product_count": _count(
            "SELECT COUNT(*) AS n FROM products WHERE status = '在售'"),
    })


@router.get("/sales")
def sales(principal: dict = Depends(auth.require_scope("dashboard:read"))):
    """销售数据：近 7 天趋势 + 商品销量排行。"""
    trend = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        n = _count(
            "SELECT COUNT(*) AS n FROM orders WHERE created_at LIKE ?", (f"{day}%",)
        )
        row = db.query_one(
            "SELECT COALESCE(SUM(total), 0) AS s FROM orders WHERE created_at LIKE ? "
            "AND status NOT IN ('待付款','已取消')",
            (f"{day}%",),
        )
        trend.append({
            "date": day,
            "order_count": n,
            "sales_amount": round(row["s"], 2) if row else 0,
        })
    top = db.query(
        "SELECT sku, name, sales, price FROM products ORDER BY sales DESC LIMIT 10"
    )
    return ok({
        "trend": trend,
        "top_products": [
            {"sku": r["sku"], "name": r["name"], "sales": r["sales"],
             "sales_amount": round(r["sales"] * r["price"], 2)}
            for r in top
        ],
    })


@router.get("/orders")
def orders(principal: dict = Depends(auth.require_scope("dashboard:read"))):
    """订单分布：各状态订单数量。"""
    rows = db.query(
        "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC"
    )
    return ok({
        "status_distribution": [
            {"status": r["status"], "count": r["n"]} for r in rows
        ],
        "total_orders": _count("SELECT COUNT(*) AS n FROM orders"),
    })


@router.get("/after-sales")
def after_sales(principal: dict = Depends(auth.require_scope("dashboard:read"))):
    """售后数据：类型 / 状态分布与待审核数。"""
    by_type = db.query(
        "SELECT type, COUNT(*) AS n FROM after_sales GROUP BY type ORDER BY n DESC"
    )
    by_status = db.query(
        "SELECT status, COUNT(*) AS n FROM after_sales GROUP BY status ORDER BY n DESC"
    )
    refund_by_status = db.query(
        "SELECT status, COUNT(*) AS n FROM refunds GROUP BY status ORDER BY n DESC"
    )
    return ok({
        "type_distribution": [{"type": r["type"], "count": r["n"]} for r in by_type],
        "status_distribution": [
            {"status": r["status"], "count": r["n"]} for r in by_status
        ],
        "refund_status_distribution": [
            {"status": r["status"], "count": r["n"]} for r in refund_by_status
        ],
        "pending_review_count": _count(
            "SELECT COUNT(*) AS n FROM after_sales WHERE status = '待审核'"),
        "pending_refund_review_count": _count(
            "SELECT COUNT(*) AS n FROM refunds WHERE status = '待审核'"),
    })


@router.get("/customer-service")
def customer_service(principal: dict = Depends(auth.require_scope("dashboard:read"))):
    """客服数据（演示指标：由工单数据推导的模拟统计）。"""
    tickets = _count("SELECT COUNT(*) AS n FROM tickets")
    closed = _count("SELECT COUNT(*) AS n FROM tickets WHERE status IN ('已解决','已关闭')")
    complaints = _count(
        "SELECT COUNT(*) AS n FROM tickets WHERE type = '投诉'")
    return ok({
        "reception_count": 128 + tickets,          # 客服接待量（演示值）
        "human_transfer_count": 23 + complaints,   # 转人工数量（演示值）
        "ai_resolution_rate": 82.5,                # AI 解决率（%）
        "satisfaction": 96.4,                      # 顾客满意度（%）
        "ticket_count": tickets,
        "ticket_resolved_count": closed,
        "complaint_count": complaints,
    })
