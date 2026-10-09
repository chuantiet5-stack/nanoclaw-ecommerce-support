"""开发模式故障模拟。

用于让 V4 演练真实商家 API 的各种异常情况：
延迟 / 超时 / 401 / 403 / 404 / 409 / 随机错误 / 服务不可用（中间件层），
库存不足 / 重复请求 / 退款风控拦截 / 物流查询失败（业务层开关）。

默认全部关闭，不影响正常演示；可通过环境变量或
PUT /mock-api/v1/admin/faults 运行时动态开启。
"""

import random
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from mock_merchant import db
from mock_merchant import response as resp

DEFAULTS = {
    "enabled": "false",
    "latency_ms": "0",
    "error_rate": "0.0",
    "force_status": "",          # 强制返回的 HTTP 状态码（如 401/403/404/409）
    "force_error_code": "",      # 强制返回的错误码
    "force_next": "-1",          # 只影响接下来 N 次请求（-1 表示一直生效）
    "service_unavailable": "false",
    "inventory_shortage": "false",
    "duplicate_request": "false",
    "refund_risk_block": "false",
    "logistics_query_fail": "false",
}

# 不参与故障模拟的路径（避免把自己锁死在门外）
WHITELIST_PREFIXES = (
    "/mock-api/v1/auth/token",
    "/mock-api/v1/admin/faults",
    "/health",
    "/mock-api/v1/health",
)


def get_config() -> dict:
    """读取当前故障配置（内存缓存 + DB 落盘）。"""
    rows = db.query("SELECT key, value FROM fault_config")
    stored = {r["key"]: r["value"] for r in rows}
    cfg = {k: stored.get(k, v) for k, v in DEFAULTS.items()}
    cfg["latency_ms"] = int(float(cfg["latency_ms"] or 0))
    cfg["error_rate"] = float(cfg["error_rate"] or 0)
    cfg["force_next"] = int(cfg["force_next"] or -1)
    for flag in ("enabled", "service_unavailable", "inventory_shortage",
                 "duplicate_request", "refund_risk_block", "logistics_query_fail"):
        cfg[flag] = str(cfg[flag]).lower() == "true"
    return cfg


def update_config(patch: dict) -> dict:
    """合并更新故障配置并落盘。"""
    for key in DEFAULTS:
        if key in patch and patch[key] is not None:
            value = patch[key]
            if isinstance(value, bool):
                value = "true" if value else "false"
            db.execute(
                "INSERT OR REPLACE INTO fault_config (key, value) VALUES (?,?)",
                (key, str(value)),
            )
    return get_config()


def reset_config() -> dict:
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR REPLACE INTO fault_config (key, value) VALUES (?,?)", (key, value))
    return get_config()


def is_flag_on(flag: str) -> bool:
    """业务层故障开关查询（仅在该开时才查库，避免影响正常性能）。"""
    row = db.query_one("SELECT value FROM fault_config WHERE key = 'enabled'")
    if not row or row["value"].lower() != "true":
        return False
    row = db.query_one("SELECT value FROM fault_config WHERE key = ?", (flag,))
    return bool(row and row["value"].lower() == "true")


def _force_consume() -> None:
    """消耗一次 force_next 计数；减到 0 时自动清除 force_status。"""
    row = db.query_one("SELECT value FROM fault_config WHERE key = 'force_next'")
    try:
        remaining = int(row["value"]) if row else -1
    except Exception:
        remaining = -1
    if remaining > 0:
        remaining -= 1
        db.execute(
            "INSERT OR REPLACE INTO fault_config (key, value) VALUES ('force_next', ?)",
            (str(remaining),),
        )
        if remaining == 0:
            db.execute("INSERT OR REPLACE INTO fault_config (key, value) VALUES ('force_status', '')")
            db.execute("INSERT OR REPLACE INTO fault_config (key, value) VALUES ('force_error_code', '')")


async def fault_middleware(request: Request, call_next):
    """API 故障模拟中间件：只作用于 /mock-api/* 路径。"""
    path = request.url.path
    if not path.startswith("/mock-api/") or any(
        path.startswith(p) for p in WHITELIST_PREFIXES
    ):
        return await call_next(request)

    cfg = get_config()
    if not cfg["enabled"]:
        return await call_next(request)

    # 1. 模拟服务不可用
    if cfg["service_unavailable"]:
        return resp.err("模拟故障：商家服务暂时不可用，请稍后重试",
                        "SERVICE_UNAVAILABLE", 503)

    # 2. 模拟延迟 / 超时
    if cfg["latency_ms"] > 0:
        time.sleep(min(cfg["latency_ms"], 60000) / 1000.0)

    # 3. 强制状态码（401/403/404/409 演练）
    if cfg["force_status"]:
        _force_consume()
        status = int(cfg["force_status"])
        code = cfg["force_error_code"] or f"SIMULATED_{status}"
        return resp.err(f"模拟故障：强制返回 HTTP {status}", code, status)

    # 4. 随机错误率
    if cfg["error_rate"] > 0 and random.random() < cfg["error_rate"]:
        return resp.err("模拟故障：随机错误（按 error_rate 概率触发）",
                        "RANDOM_ERROR", 500)

    return await call_next(request)


def register_middlewares(app) -> None:
    """注册中间件：request_id（最外层）+ 故障模拟。"""

    @app.middleware("http")
    async def _fault(request: Request, call_next):
        return await fault_middleware(request, call_next)

    @app.middleware("http")
    async def _rid(request: Request, call_next):
        rid = request.headers.get("X-Request-Id") or resp.get_request_id()
        resp.set_request_id(rid)
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response
