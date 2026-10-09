"""路由聚合：全部业务路由统一挂载到 /mock-api/v1 前缀。"""

from mock_merchant.routes import (
    admin,
    after_sales,
    auth,
    customers,
    dashboard,
    inventory,
    logistics,
    orders,
    products,
    refunds,
    tickets,
)

ALL_ROUTERS = [
    auth.router,
    products.router,
    inventory.router,
    orders.router,
    logistics.router,
    after_sales.router,
    refunds.router,
    tickets.router,
    customers.router,
    dashboard.router,
    admin.router,
]
