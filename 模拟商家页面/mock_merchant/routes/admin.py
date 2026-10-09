"""管理 API：故障模拟开关 + 操作审计日志。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from mock_merchant import auth, db
from mock_merchant import faults
from mock_merchant.response import AppError, ok

router = APIRouter(prefix="/admin", tags=["系统管理"])


class FaultUpdate(BaseModel):
    enabled: bool | None = None
    latency_ms: int | None = None
    error_rate: float | None = None
    force_status: int | None = None
    force_error_code: str | None = None
    force_next: int | None = None
    service_unavailable: bool | None = None
    inventory_shortage: bool | None = None
    duplicate_request: bool | None = None
    refund_risk_block: bool | None = None
    logistics_query_fail: bool | None = None


@router.get("/faults")
def get_faults(principal: dict = Depends(auth.require_scope("faults:read"))):
    """查看当前故障模拟配置。"""
    return ok(faults.get_config())


@router.put("/faults")
def update_faults(body: FaultUpdate,
                  principal: dict = Depends(auth.current_principal)):
    """更新故障模拟配置（仅商家管理员）。

    示例：
    - 模拟 401：{"enabled": true, "force_status": 401}
    - 模拟延迟：{"enabled": true, "latency_ms": 2000}
    - 模拟库存不足：{"enabled": true, "inventory_shortage": true}
    - 模拟退款风控拦截：{"enabled": true, "refund_risk_block": true}
    """
    if principal["role"] != "merchant_admin":
        raise AppError("故障模拟配置仅商家管理员可修改", "FORBIDDEN", 403)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = faults.update_config(patch)
    return ok(cfg, "故障模拟配置已更新")


@router.post("/faults/reset")
def reset_faults(principal: dict = Depends(auth.current_principal)):
    """重置全部故障模拟（恢复正常演示）。"""
    if principal["role"] != "merchant_admin":
        raise AppError("故障模拟配置仅商家管理员可修改", "FORBIDDEN", 403)
    cfg = faults.reset_config()
    return ok(cfg, "故障模拟已全部关闭")


@router.get("/op-logs")
def op_logs(
    action: str = Query(default=""),
    actor: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    principal: dict = Depends(auth.require_staff),
):
    """操作审计日志（所有写操作与登录事件都会记录）。"""
    where, params = ["1=1"], []
    if action:
        where.append("action LIKE ?")
        params.append(f"%{action}%")
    if actor:
        where.append("actor LIKE ?")
        params.append(f"%{actor}%")
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM op_logs WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM op_logs WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [
            {"id": r["id"], "actor": r["actor"], "role": r["role"],
             "action": r["action"], "resource": r["resource"], "detail": r["detail"],
             "request_id": r["request_id"], "created_at": r["created_at"]}
            for r in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    })
