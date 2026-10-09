"""工单管理 API：列表 / 详情 / 创建 / 分配 / 回复 / 关闭。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from mock_merchant import auth, db, rules
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers

router = APIRouter(prefix="/tickets", tags=["工单管理"])


class TicketCreate(BaseModel):
    customer_id: str
    type: str  # 咨询/售后/投诉/建议/其他
    description: str = Field(min_length=1)
    order_id: str = ""
    priority: int = Field(default=2, ge=1, le=3)  # 1低 2普通 3加急
    summary: str = ""


class AssignRequest(BaseModel):
    assignee: str = Field(min_length=1, description="分配给的客服姓名/账号")


class ReplyRequest(BaseModel):
    content: str = Field(min_length=1)


class CloseRequest(BaseModel):
    resolution: str = ""


def _get_ticket(ticket_id: str) -> dict:
    row = db.query_one("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    if not row:
        raise AppError(f"工单 {ticket_id} 不存在", "TICKET_NOT_FOUND", 404)
    return row


def _check_access(principal: dict, ticket: dict) -> None:
    if principal["type"] == "customer" and principal["subject_id"] != ticket["customer_id"]:
        raise AppError("无权访问该工单", "TICKET_ACCESS_DENIED", 403)


def _append_record(ticket_id: str, note: str) -> None:
    row = _get_ticket(ticket_id)
    records = db.loads(row["records"], [])
    records.append({"time": rules.now_str(), "note": note})
    db.execute(
        "UPDATE tickets SET records = ?, updated_at = ? WHERE ticket_id = ?",
        (db.dumps(records), rules.now_str(), ticket_id),
    )


def _detail(row: dict) -> dict:
    data = helpers.ticket_dict(row)
    customer = db.query_one(
        "SELECT name FROM customers WHERE customer_id = ?", (row["customer_id"],)
    )
    data["customer_name"] = customer["name"] if customer else ""
    return data


@router.get("")
def list_tickets(
    status: str = Query(default=""),
    type: str = Query(default=""),
    customer_id: str = Query(default=""),
    priority: int = Query(default=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("tickets:read")),
):
    """工单列表。顾客 Token 自动只返回本人工单。"""
    if principal["type"] == "customer":
        customer_id = principal["subject_id"]
    where, params = ["1=1"], []
    if status:
        where.append("status = ?")
        params.append(status)
    if type:
        where.append("type = ?")
        params.append(type)
    if customer_id:
        where.append("customer_id = ?")
        params.append(customer_id)
    if priority:
        where.append("priority = ?")
        params.append(priority)
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM tickets WHERE {cond}", params)["n"]
    rows = db.query(
        f"SELECT * FROM tickets WHERE {cond} ORDER BY priority DESC, id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.ticket_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


@router.get("/{ticket_id}")
def get_ticket(ticket_id: str,
               principal: dict = Depends(auth.require_scope("tickets:read"))):
    """工单详情（含处理记录）。"""
    row = _get_ticket(ticket_id)
    _check_access(principal, row)
    return ok(_detail(row))


@router.post("")
def create_ticket(body: TicketCreate,
                  principal: dict = Depends(auth.require_scope("tickets:create"))):
    """创建工单（顾客可自行创建；员工可代客创建）。"""
    if body.type not in rules.TICKET_TYPES:
        raise AppError(f"工单类型仅支持：{'、'.join(rules.TICKET_TYPES)}", "INVALID_TYPE", 400)
    if principal["type"] == "customer" and principal["subject_id"] != body.customer_id:
        raise AppError("无权为其他顾客创建工单", "FORBIDDEN", 403)
    customer = db.query_one(
        "SELECT * FROM customers WHERE customer_id = ?", (body.customer_id,)
    )
    if not customer:
        raise AppError(f"顾客 {body.customer_id} 不存在", "CUSTOMER_NOT_FOUND", 404)
    if body.order_id:
        if not db.query_one("SELECT 1 AS x FROM orders WHERE order_id = ?", (body.order_id,)):
            raise AppError(f"订单 {body.order_id} 不存在", "ORDER_NOT_FOUND", 404)

    ticket_id = rules.new_ticket_id()
    now = rules.now_str()
    db.execute(
        """INSERT INTO tickets (ticket_id, customer_id, order_id, type, status, priority,
           priority_label, summary, description, records, created_at, updated_at)
           VALUES (?,?,?,?,'待处理',?,?,?,?,?,?,?)""",
        (ticket_id, body.customer_id, body.order_id, body.type, body.priority,
         rules.ticket_priority_label(body.priority),
         body.summary or body.description[:20], body.description,
         db.dumps([{"time": now, "note": "工单已创建，等待客服受理"}]), now, now),
    )
    helpers.log_op(principal, "创建工单", f"tickets:{ticket_id}",
                   f"{body.type}工单：{body.description[:50]}")
    return ok(_detail(_get_ticket(ticket_id)), "工单创建成功", 201)


@router.post("/{ticket_id}/assign")
def assign(ticket_id: str, body: AssignRequest,
           principal: dict = Depends(auth.require_scope("tickets:write"))):
    """分配工单给客服：待处理/处理中 → 处理中。"""
    row = _get_ticket(ticket_id)
    if row["status"] in ("已关闭",):
        raise AppError("工单已关闭，无法分配", "TICKET_CLOSED", 409)
    db.execute(
        "UPDATE tickets SET assigned_to = ?, status = '处理中', updated_at = ? "
        "WHERE ticket_id = ?",
        (body.assignee, rules.now_str(), ticket_id),
    )
    _append_record(ticket_id, f"工单已分配给 {body.assignee}")
    helpers.log_op(principal, "分配工单", f"tickets:{ticket_id}", f"分配给 {body.assignee}")
    return ok(_detail(_get_ticket(ticket_id)), f"工单已分配给 {body.assignee}")


@router.post("/{ticket_id}/reply")
def reply(ticket_id: str, body: ReplyRequest,
          principal: dict = Depends(auth.require_scope("tickets:write"))):
    """客服回复工单（追加处理记录）。"""
    row = _get_ticket(ticket_id)
    if row["status"] == "已关闭":
        raise AppError("工单已关闭，无法回复", "TICKET_CLOSED", 409)
    status = "处理中" if row["status"] == "待处理" else row["status"]
    db.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE ticket_id = ?",
        (status, rules.now_str(), ticket_id),
    )
    _append_record(ticket_id, f"{principal['username']} 回复：{body.content}")
    helpers.log_op(principal, "回复工单", f"tickets:{ticket_id}", body.content[:50])
    return ok(_detail(_get_ticket(ticket_id)), "回复成功")


@router.post("/{ticket_id}/close")
def close(ticket_id: str, body: CloseRequest | None = None,
          principal: dict = Depends(auth.require_scope("tickets:write"))):
    """关闭工单（可附处理结果说明）。"""
    row = _get_ticket(ticket_id)
    if row["status"] == "已关闭":
        raise AppError("工单已关闭，无需重复操作", "TICKET_CLOSED", 409)
    db.execute(
        "UPDATE tickets SET status = '已关闭', updated_at = ? WHERE ticket_id = ?",
        (rules.now_str(), ticket_id),
    )
    resolution = (body.resolution if body else "") or "问题已处理，工单关闭"
    _append_record(ticket_id, resolution)
    helpers.log_op(principal, "关闭工单", f"tickets:{ticket_id}", resolution)
    return ok(_detail(_get_ticket(ticket_id)), "工单已关闭")
