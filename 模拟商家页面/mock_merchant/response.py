"""统一 API 响应格式与业务异常。

成功：{"success": true, "data": {}, "message": "操作成功", "request_id": "req_xxx", "timestamp": "..."}
失败：{"success": false, "data": null, "message": "错误说明", "error_code": "XXX", "request_id": "req_xxx", "timestamp": "..."}
"""

import contextvars
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_merchant import rules

# 每个请求的唯一 ID（由中间件写入）
_current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def set_request_id(rid: str) -> None:
    _current_request_id.set(rid)


def get_request_id() -> str:
    return _current_request_id.get() or rules.request_id()


def _envelope(success: bool, data, message: str, error_code: str | None) -> dict:
    body = {
        "success": success,
        "data": data,
        "message": message,
        "request_id": get_request_id(),
        "timestamp": rules.utc_now_iso(),
    }
    if error_code is not None:
        body["error_code"] = error_code
    return body


def ok(data=None, message: str = "操作成功", status_code: int = 200) -> JSONResponse:
    """成功响应。"""
    return JSONResponse(status_code=status_code, content=_envelope(True, data, message, None))


def err(message: str, error_code: str = "INTERNAL_ERROR", status_code: int = 400) -> JSONResponse:
    """失败响应。"""
    return JSONResponse(
        status_code=status_code,
        content=_envelope(False, None, message, error_code),
    )


class AppError(Exception):
    """业务异常：路由内 raise 即可返回统一错误响应。"""

    def __init__(self, message: str, error_code: str = "BAD_REQUEST", status_code: int = 400):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(message)


# ---------- 常用错误码 ----------

UNAUTHORIZED = ("未登录或 Token 无效，请先调用 /auth/token 获取令牌", "UNAUTHORIZED", 401)
FORBIDDEN = ("当前角色无权执行该操作", "FORBIDDEN", 403)


def not_found(resource: str, key: str) -> AppError:
    return AppError(f"{resource} {key} 不存在", f"{resource.upper()}_NOT_FOUND", 404)


def register_exception_handlers(app: FastAPI) -> None:
    """全局异常处理：所有错误都以统一 JSON 结构返回。"""
    from mock_merchant import rules as _rules

    @app.exception_handler(AppError)
    async def _app_error_handler(request, exc: AppError):
        return err(exc.message, exc.error_code, exc.status_code)

    @app.exception_handler(_rules.OrderStatusError)
    async def _order_status_error_handler(request, exc: _rules.OrderStatusError):
        return err(str(exc), "INVALID_STATUS_TRANSITION", 409)

    @app.exception_handler(_rules.AfterSaleStatusError)
    async def _as_status_error_handler(request, exc: _rules.AfterSaleStatusError):
        return err(str(exc), "INVALID_STATUS_TRANSITION", 409)

    @app.exception_handler(_rules.RefundStatusError)
    async def _refund_status_error_handler(request, exc: _rules.RefundStatusError):
        return err(str(exc), "INVALID_STATUS_TRANSITION", 409)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request, exc: StarletteHTTPException):
        mapping = {
            401: "未登录或 Token 无效，请先调用 /auth/token 获取令牌",
            403: "当前角色无权执行该操作",
            404: "接口不存在，请检查请求路径",
            405: "请求方法不被允许",
            422: "请求参数校验失败",
        }
        message = mapping.get(exc.status_code, f"请求失败（HTTP {exc.status_code}）")
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
                405: "METHOD_NOT_ALLOWED", 422: "VALIDATION_ERROR"}.get(
            exc.status_code, f"HTTP_{exc.status_code}")
        return err(message, code, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request, exc: RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', [])[1:])}: {e.get('msg', '')}"
            for e in exc.errors()
        )
        return err(f"请求参数校验失败：{detail}", "VALIDATION_ERROR", 422)

    @app.exception_handler(Exception)
    async def _internal_error_handler(request, exc: Exception):
        return err(f"服务器内部错误：{exc}", "INTERNAL_ERROR", 500)
