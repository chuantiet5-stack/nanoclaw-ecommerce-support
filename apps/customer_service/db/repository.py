"""数据访问层（生产落地 P0-1.1 步骤 5）。

所有业务工具未来通过此层读写 MySQL，替代 store.py 的 JSON 文件读写。
接口命名与 store.py 的业务语义保持一致（find_order / create_ticket 等），
便于平滑切换。写操作使用事务，保证原子性。
"""

from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import (Goods, Logistics, Order, RefundAuditLog,
                       RefundRecord, Ticket, User, UserPreference)


# ========== 会话管理 ==========

def get_session() -> AsyncSession:
    """获取一个新的数据库会话（使用后需自行 close）。"""
    return AsyncSessionLocal()


# ========== 用户 ==========

async def get_user_by_username(username: str) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()


async def get_user_by_customer_id(customer_id: str) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.customer_id == customer_id)
        )
        return result.scalar_one_or_none()


# ========== 商品 ==========

async def search_goods(keyword: str = "", category: str = "",
                       limit: int = 20) -> list[dict]:
    async with AsyncSessionLocal() as session:
        stmt = select(Goods)
        if keyword:
            stmt = stmt.where(Goods.name.like(f"%{keyword}%"))
        if category:
            stmt = stmt.where(Goods.category == category)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return [_goods_to_dict(g) for g in result.scalars().all()]


async def get_goods_by_sku(sku: str) -> Optional[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Goods).where(Goods.sku == sku)
        )
        g = result.scalar_one_or_none()
        return _goods_to_dict(g) if g else None


def _goods_to_dict(g: Goods) -> dict:
    """Goods 行 → goods.json 同构 dict（sku/highlights/sizes/scenes/link）。"""
    return {
        "sku": g.sku,
        "name": g.name,
        "category": g.category,
        "price": g.price,
        "highlights": g.highlights or "",
        "description": g.description or "",
        "stock": g.stock,
        "sizes": g.sizes,
        "scenes": g.scenes,
        "link": g.link or "",
    }


# ========== 订单 ==========

async def find_order(order_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order).where(Order.order_id == order_id)
        )
        o = result.scalar_one_or_none()
        return _order_to_dict(o) if o else None


async def list_orders_by_customer(customer_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc())
        )
        return [_order_to_dict(o) for o in result.scalars().all()]


async def update_order_address(order_id: str, new_address: str) -> bool:
    """更新订单地址（使用事务，原子操作，替代 JSON 文件的 load→modify→save）。"""
    async with AsyncSessionLocal() as session:
        async with session.begin():  # 开启事务
            result = await session.execute(
                update(Order)
                .where(Order.order_id == order_id)
                .values(address=new_address)
            )
            return result.rowcount > 0


def _order_to_dict(o: Order) -> dict:
    return {
        "order_id": o.order_id,
        "customer_id": o.customer_id,
        "status": o.status,
        "total": o.total,
        "receiver": {
            "name": o.receiver_name,
            "phone": o.receiver_phone,
            "address": o.address,
        },
        "items": o.items,
        "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
        "paid_at": o.paid_at.strftime("%Y-%m-%d %H:%M:%S") if o.paid_at else "",
        "signed_at": o.signed_at.strftime("%Y-%m-%d %H:%M:%S") if o.signed_at else "",
    }


# ========== 工单 ==========

async def create_ticket(ticket_id: str, customer_id: str, order_id: str,
                        ticket_type: str, summary: str) -> dict:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            ticket = Ticket(
                ticket_id=ticket_id,
                customer_id=customer_id,
                order_id=order_id,
                type=ticket_type,
                summary=summary,
                status="待处理",
            )
            session.add(ticket)
        return {
            "ticket_id": ticket_id,
            "status": "待处理",
            "summary": summary,
        }


async def get_ticket(ticket_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        )
        t = result.scalar_one_or_none()
        if not t:
            return None
        return {
            "ticket_id": t.ticket_id,
            "status": t.status,
            "type": t.type,
            "summary": t.summary,
            "created_at": (
                t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else ""
            ),
        }


# ========== 退款 ==========

async def count_refunds_by_customer(customer_id: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(RefundRecord.id))
            .where(RefundRecord.customer_id == customer_id)
        )
        return result.scalar() or 0


async def count_recent_auto_refunds(customer_id: str, since: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(RefundRecord.id)).where(
                RefundRecord.customer_id == customer_id,
                RefundRecord.decided_by == "auto",
                RefundRecord.created_at >= since,
            )
        )
        return result.scalar() or 0


async def save_refund_record(record: dict) -> None:
    """追加退款记录（JSON 键名兼容入口，等价 append_refund_record）。"""
    await append_refund_record(record)


# ========== 偏好记忆 ==========

async def list_preferences(customer_id: str) -> list[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserPreference.content)
            .where(UserPreference.customer_id == customer_id)
            .order_by(UserPreference.id)
        )
        return [row[0] for row in result.all()]


async def append_preference(customer_id: str, content: str) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(UserPreference(customer_id=customer_id, content=content))


# =====================================================================
# 以下为 JSON 兼容层（生产落地 P0-1.1：store.py 委托所需）
# 返回值与 data/*.json 的数据形状完全一致（dict 键名、嵌套结构），
# 供 agent/tools/ecom/store_mysql.py 同步调用，业务工具无感知切换。
# =====================================================================

def _parse_dt(value) -> "datetime | None":
    """'YYYY-MM-DD HH:MM:SS' → datetime；空/非法返回 None。"""
    from datetime import datetime as _dt
    if not value:
        return None
    if isinstance(value, _dt):
        return value
    try:
        return _dt.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _fmt_dt(value) -> str:
    """datetime → 'YYYY-MM-DD HH:MM:SS'；None → ''。"""
    from datetime import datetime as _dt
    if isinstance(value, _dt):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")


def _user_to_dict(u: User) -> dict:
    """User 行 → users.json 档案同构 dict（不含密码，鉴权走 password_hash）。"""
    return {
        "username": u.username,
        "customer_id": u.customer_id,
        "name": u.name,
        "level": u.level or "普通会员",
        "points": u.points or 0,
        "role": u.role or "user",
    }


async def list_all_users() -> list[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).order_by(User.id))
        return list(result.scalars().all())


async def list_goods() -> list[dict]:
    """全部商品（与 goods.json 同构，保留原文件顺序感由 id 保证）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Goods).order_by(Goods.id))
        return [_goods_to_dict(g) for g in result.scalars().all()]


async def list_all_orders() -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).order_by(Order.id))
        return [_order_to_dict(o) for o in result.scalars().all()]


async def upsert_order(order: dict) -> None:
    """按 order_id 插入或更新订单（save_orders 委托的落点）。"""
    receiver = order.get("receiver", {}) or {}
    values = dict(
        customer_id=order.get("customer_id", ""),
        status=order.get("status", ""),
        total=float(order.get("total", 0)),
        receiver_name=receiver.get("name", ""),
        receiver_phone=receiver.get("phone", ""),
        address=receiver.get("address", ""),
        items=order.get("items"),
        created_at=_parse_dt(order.get("created_at")),
        paid_at=_parse_dt(order.get("paid_at")),
        signed_at=_parse_dt(order.get("signed_at")),
    )
    async with AsyncSessionLocal() as session:
        async with session.begin():
            existing = await session.execute(
                select(Order).where(Order.order_id == order["order_id"])
            )
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(Order(order_id=order["order_id"], **values))
            else:
                for k, v in values.items():
                    setattr(row, k, v)


def _ticket_to_dict(t: Ticket) -> dict:
    """Ticket 行 → tickets.json 同构 dict。"""
    return {
        "ticket_id": t.ticket_id,
        "type": t.type,
        "order_id": t.order_id or None,
        "customer_id": t.customer_id,
        "description": t.description or "",
        "status": t.status,
        "priority": t.priority_label or "普通",
        "created_at": _fmt_dt(t.created_at),
        "updated_at": _fmt_dt(t.updated_at),
        "records": t.records or [],
    }


async def list_tickets() -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Ticket).order_by(Ticket.id))
        return [_ticket_to_dict(t) for t in result.scalars().all()]


async def upsert_ticket(ticket: dict) -> None:
    """按 ticket_id 插入或更新工单（save_tickets 委托的落点）。"""
    label = str(ticket.get("priority", "普通")) or "普通"
    values = dict(
        customer_id=ticket.get("customer_id", ""),
        order_id=ticket.get("order_id") or "",
        type=ticket.get("type", "咨询"),
        status=ticket.get("status", "待处理"),
        priority=2 if label == "加急" else 1,
        summary=str(ticket.get("description", ""))[:512],
        description=ticket.get("description", ""),
        priority_label=label,
        records=ticket.get("records"),
        created_at=_parse_dt(ticket.get("created_at")),
        updated_at=_parse_dt(ticket.get("updated_at")),
    )
    async with AsyncSessionLocal() as session:
        async with session.begin():
            existing = await session.execute(
                select(Ticket).where(Ticket.ticket_id == ticket["ticket_id"])
            )
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(Ticket(ticket_id=ticket["ticket_id"], **values))
            else:
                for k, v in values.items():
                    setattr(row, k, v)


async def list_ticket_ids() -> set[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Ticket.ticket_id))
        return {row[0] for row in result.all()}


async def list_logistics_map() -> dict[str, list]:
    """物流轨迹 → {order_id: traces}（与 logistics.json 同构）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Logistics).order_by(Logistics.id))
        return {row.order_id: (row.traces or []) for row in result.scalars().all()}


async def upsert_logistics(order_id: str, traces: list) -> None:
    """按 order_id 插入或更新物流轨迹（save_logistics 委托的落点）。"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            existing = await session.execute(
                select(Logistics).where(Logistics.order_id == order_id)
            )
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(Logistics(
                    order_id=order_id,
                    status=(traces[-1].get("status", "") if traces else "待发货"),
                    traces=traces,
                ))
            else:
                row.traces = traces
                if traces:
                    row.status = traces[-1].get("status", row.status)


def _refund_to_dict(r: RefundRecord) -> dict:
    """RefundRecord 行 → refund_records.json 同构 dict。"""
    return {
        "refund_no": r.refund_id,
        "order_id": r.order_id,
        "customer_id": r.customer_id,
        "amount": r.amount,
        "reason": r.reason or "",
        "decided_by": r.decided_by,
        "rule": r.rule_hit or "",
        "created_at": _fmt_dt(r.created_at),
    }


async def list_refund_records() -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RefundRecord).order_by(RefundRecord.id)
        )
        return [_refund_to_dict(r) for r in result.scalars().all()]


async def append_refund_record(record: dict) -> None:
    """追加退款记录（键名与 refund_records.json 对齐）。"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(RefundRecord(
                refund_id=record.get("refund_no", ""),
                order_id=record.get("order_id", ""),
                customer_id=record.get("customer_id", ""),
                amount=float(record.get("amount", 0)),
                reason=str(record.get("reason", ""))[:255],
                decided_by=record.get("decided_by", "auto"),
                decision="同意",
                rule_hit=str(record.get("rule", ""))[:16],
                created_at=_parse_dt(record.get("created_at")),
            ))


async def list_audit_log() -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RefundAuditLog).order_by(RefundAuditLog.id)
        )
        return [
            {
                "time": _fmt_dt(a.created_at),
                "order_id": a.order_id,
                "customer_id": a.customer_id,
                "amount": a.amount if a.amount is not None else 0,
                "reason": a.reason or "",
                "action": a.action,
                "rule": a.rule_hit or "",
                "detail": a.detail or "",
                "profile": a.profile_snapshot,
            }
            for a in result.scalars().all()
        ]


async def append_audit_record(entry: dict) -> None:
    """追加审计日志（键名与 refund_policy.append_audit 的 entry 对齐）。"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(RefundAuditLog(
                refund_id="",
                customer_id=entry.get("customer_id", ""),
                order_id=entry.get("order_id", ""),
                action=entry.get("action", ""),
                reason=str(entry.get("reason", ""))[:512],
                rule_hit=str(entry.get("rule", ""))[:16],
                profile_snapshot=entry.get("profile"),
                detail=entry.get("detail", ""),
                amount=float(entry.get("amount", 0) or 0),
                created_at=_parse_dt(entry.get("time")),
            ))


async def check_connection() -> bool:
    """连通性预检：SELECT 1（启动时 DATA_BACKEND=mysql 门禁用）。"""
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
