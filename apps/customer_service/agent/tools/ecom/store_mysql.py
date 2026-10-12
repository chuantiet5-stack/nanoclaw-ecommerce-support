"""store.py 的 MySQL 委托层（生产落地 P0-1.1）。

DATA_BACKEND=mysql 时，store.py 的数据函数委托到本模块；
本模块经 db.sync_bridge（独立线程事件循环）同步调用 db.repository
的异步函数，返回值与 JSON 文件的数据形状完全一致——业务工具无感知。

仅 DATA_BACKEND=mysql 时才会被 import（惰性导入），JSON 模式零开销。
"""

import os
from datetime import datetime

from db import repository
from db.sync_bridge import run_async


def load_users() -> dict:
    """全部用户 → {username: 档案}，附 __default__（默认演示账号）。"""
    rows = run_async(repository.list_all_users())
    users = {u.username: _user_dict(u) for u in rows}
    users["__default__"] = os.getenv("DEMO_DEFAULT_USER", "han")
    return users


def _user_dict(u) -> dict:
    return {
        "username": u.username,
        "customer_id": u.customer_id,
        "name": u.name,
        "level": u.level or "普通会员",
        "points": u.points or 0,
        "role": u.role or "user",
    }


def get_user(actor):
    if not actor:
        return None
    row = run_async(repository.get_user_by_username(actor))
    return _user_dict(row) if row else None


def load_goods() -> list:
    return run_async(repository.list_goods())


def load_orders() -> list:
    return run_async(repository.list_all_orders())


def save_orders(orders: list) -> None:
    """批量落库：按 order_id 逐条 upsert（事务内原子完成）。"""
    for order in orders:
        run_async(repository.upsert_order(order))


def find_order(order_id: str):
    return run_async(repository.find_order(order_id))


def load_logistics() -> dict:
    return run_async(repository.list_logistics_map())


def save_logistics(logistics: dict) -> None:
    """批量落库：按 order_id 逐条 upsert 物流轨迹。"""
    for order_id, traces in logistics.items():
        run_async(repository.upsert_logistics(order_id, traces))


def load_tickets() -> list:
    return run_async(repository.list_tickets())


def save_tickets(tickets: list) -> None:
    """批量落库：按 ticket_id 逐条 upsert。"""
    for ticket in tickets:
        run_async(repository.upsert_ticket(ticket))


def new_ticket_id() -> str:
    """生成不与现有工单冲突的工单号（与 store.py 同算法）。"""
    existing = run_async(repository.list_ticket_ids())
    base = datetime.now().strftime("%Y%m%d%H%M%S")
    ticket_id = f"TK{base}"
    suffix = 1
    while ticket_id in existing:
        suffix += 1
        ticket_id = f"TK{base}{suffix:02d}"
    return ticket_id


# ---------- 账号记忆（user_preferences 表，按 customer_id 归档） ----------

def _customer_id_of(actor: str) -> str | None:
    row = run_async(repository.get_user_by_username(actor))
    return row.customer_id if row else None


def load_memory(actor: str) -> list:
    """偏好记忆（与 memory/{actor}.md 的行内容同构，含日期后缀）。"""
    customer_id = _customer_id_of(actor)
    if not customer_id:
        return []
    return run_async(repository.list_preferences(customer_id))


def append_memory(actor: str, content: str) -> None:
    customer_id = _customer_id_of(actor)
    if not customer_id:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    run_async(repository.append_preference(
        customer_id, f"{content}（{today} 记录）"
    ))
