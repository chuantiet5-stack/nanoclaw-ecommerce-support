"""模拟商家服务 FastAPI 应用工厂。

- API 统一前缀：/mock-api/v1
- 商家后台：/merchant
- 健康检查：/health 与 /mock-api/v1/health
- 启动时自动初始化 SQLite 与种子数据
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse

import config
from mock_merchant import db
from mock_merchant import faults
from mock_merchant import response as resp
from mock_merchant.routes import ALL_ROUTERS
from mock_merchant.web import pages


def create_app() -> FastAPI:
    app = FastAPI(
        title="模拟商家 API 服务（Mock Merchant）",
        description=(
            "独立模拟商家系统：供 V4 电商客服 Agent 通过 HTTP 调用，"
            "演示商品 / 库存 / 订单 / 物流 / 售后 / 退款 / 工单 / 顾客 / 看板全流程。"
            "\n\n演示账号：merchant_admin / 123456（管理员）、operator / 123456（运营）、"
            "after_sales / 123456（售后客服）、han / 123456（顾客 C001）、lei / 123456（顾客 C002）"
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 启动即初始化数据库与种子数据（幂等：空库才写入）
    db.init_db()

    # 中间件：request_id + 故障模拟
    faults.register_middlewares(app)

    # 全局异常 → 统一 JSON 结构
    resp.register_exception_handlers(app)

    # 业务路由（统一前缀 /mock-api/v1）
    for router in ALL_ROUTERS:
        app.include_router(router, prefix=config.API_PREFIX)

    # 商家后台页面
    app.include_router(pages.router)

    @app.get("/health", tags=["系统"])
    @app.get(f"{config.API_PREFIX}/health", tags=["系统"])
    def health():
        return JSONResponse(content={
            "success": True,
            "data": {"status": "ok", "service": "mock-merchant",
                     "version": "1.0.0"},
            "message": "服务运行中",
            "request_id": resp.get_request_id(),
            "timestamp": resp.rules.utc_now_iso(),
        })

    @app.get("/", tags=["系统"], include_in_schema=False)
    def index():
        """访问根路径直接跳转商家后台（避免误以为服务未启动）。"""
        return RedirectResponse(url=config.MERCHANT_PAGE_PATH, status_code=302)

    return app
