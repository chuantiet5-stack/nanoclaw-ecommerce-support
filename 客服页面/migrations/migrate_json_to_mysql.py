"""将 data/*.json 模拟数据导入 MySQL（一次性迁移脚本，生产落地 P0-1.1 步骤 8）。

前置条件：
1. MySQL 已启动且执行过 migrations/001_init.sql 建表
2. 环境变量 DB_URL 已配置（或修改 db/database.py 默认值）

执行方式：
    python migrations/migrate_json_to_mysql.py

说明：
- 用户密码在迁移时由明文转为 bcrypt 哈希（P0-1.2 步骤 2）
- 账号偏好记忆（data/memory/*.md）迁移到 user_preferences 表，
  按用户名 → customer_id 归档，行内容与 md 文件行格式一致
- 已存在的数据按业务主键（username/order_id/ticket_id 等）跳过，
  脚本可重复执行（幂等）
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# 允许以 `python migrations/migrate_json_to_mysql.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from db.database import AsyncSessionLocal  # noqa: E402
from db.models import (Goods, Logistics, Order, RefundAuditLog,  # noqa: E402
                       RefundRecord, Ticket, User, UserPreference)
from security.password import hash_password  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load(filename: str, default):
    path = DATA_DIR / filename
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


async def _exists(session, model, **filters) -> bool:
    result = await session.execute(select(model).filter_by(**filters).limit(1))
    return result.scalar_one_or_none() is not None


async def migrate():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. 迁移用户（已是 bcrypt 哈希的保持原样，明文才哈希；含 level/points/role）
            users_data = _load("users.json", {"users": []})
            n = 0
            for u in users_data.get("users", []):
                if await _exists(session, User, username=u["username"]):
                    continue
                raw_pw = str(u.get("password", ""))
                # 防双重哈希：$2b$/$2a$ 前缀的已是 bcrypt，直接入库
                pw_hash = raw_pw if raw_pw.startswith(("$2b$", "$2a$")) \
                    else hash_password(raw_pw)
                session.add(User(
                    customer_id=u["customer_id"],
                    username=u["username"],
                    password_hash=pw_hash,
                    name=u.get("name", u["username"]),
                    phone=u.get("phone", ""),
                    level=u.get("level", "普通会员"),
                    points=u.get("points", 0),
                    role=u.get("role", "user"),
                ))
                n += 1
            print(f"已迁移 {n} 个用户（跳过已存在）")

            # 2. 迁移商品（字段与 goods.json 对齐：sku/highlights/sizes/scenes/link）
            n = 0
            for g in _load("goods.json", []):
                if await _exists(session, Goods, sku=g["sku"]):
                    continue
                session.add(Goods(
                    sku=g["sku"],
                    name=g["name"],
                    category=g.get("category", ""),
                    price=g["price"],
                    highlights=g.get("highlights", ""),
                    description=g.get("description", ""),
                    stock=g.get("stock", 0),
                    sizes=g.get("sizes"),
                    scenes=g.get("scenes"),
                    link=g.get("link", ""),
                ))
                n += 1
            print(f"已迁移 {n} 个商品")

            # 3. 迁移订单（含时间字段）
            n = 0
            for o in _load("orders.json", []):
                if await _exists(session, Order, order_id=o["order_id"]):
                    continue
                receiver = o.get("receiver", {})
                session.add(Order(
                    order_id=o["order_id"],
                    customer_id=o["customer_id"],
                    status=o["status"],
                    total=o["total"],
                    receiver_name=receiver.get("name", ""),
                    receiver_phone=receiver.get("phone", ""),
                    address=receiver.get("address", ""),
                    items=o.get("items"),
                    created_at=_parse_dt(o.get("created_at")),
                    paid_at=_parse_dt(o.get("paid_at")),
                    signed_at=_parse_dt(o.get("signed_at")),
                ))
                n += 1
            print(f"已迁移 {n} 个订单")

            # 4. 迁移物流轨迹（logistics.json: {order_id: [traces]}）
            n = 0
            for order_id, traces in _load("logistics.json", {}).items():
                if await _exists(session, Logistics, order_id=order_id):
                    continue
                session.add(Logistics(order_id=order_id, traces=traces))
                n += 1
            print(f"已迁移 {n} 条物流轨迹")

            # 5. 迁移工单（完整字段：description/priority/records/时间）
            n = 0
            for t in _load("tickets.json", []):
                if await _exists(session, Ticket, ticket_id=t["ticket_id"]):
                    continue
                label = str(t.get("priority", "普通")) or "普通"
                session.add(Ticket(
                    ticket_id=t["ticket_id"],
                    customer_id=t.get("customer_id", ""),
                    order_id=t.get("order_id") or "",
                    type=t.get("type", "咨询"),
                    status=t.get("status", "待处理"),
                    priority=2 if label == "加急" else 1,
                    summary=str(t.get("description", ""))[:512],
                    description=t.get("description", ""),
                    priority_label=label,
                    records=t.get("records"),
                    created_at=_parse_dt(t.get("created_at")),
                    updated_at=_parse_dt(t.get("updated_at")),
                ))
                n += 1
            print(f"已迁移 {n} 个工单")

            # 6. 迁移退款记录（键名映射：refund_no→refund_id, rule→rule_hit）
            n = 0
            for r in _load("refund_records.json", []):
                if await _exists(session, RefundRecord, refund_id=r.get("refund_no", "")):
                    continue
                session.add(RefundRecord(
                    refund_id=r.get("refund_no", ""),
                    order_id=r.get("order_id", ""),
                    customer_id=r.get("customer_id", ""),
                    amount=float(r.get("amount", 0)),
                    reason=str(r.get("reason", ""))[:255],
                    decided_by=r.get("decided_by", "auto"),
                    decision="同意",
                    rule_hit=str(r.get("rule", ""))[:16],
                    created_at=_parse_dt(r.get("created_at")),
                ))
                n += 1
            print(f"已迁移 {n} 条退款记录")

            # 7. 迁移退款审计日志
            n = 0
            for a in _load("refund_audit_log.json", []):
                session.add(RefundAuditLog(
                    refund_id="",
                    customer_id=a.get("customer_id", ""),
                    order_id=a.get("order_id", ""),
                    action=a.get("action", ""),
                    reason=str(a.get("reason", ""))[:512],
                    rule_hit=str(a.get("rule", ""))[:16],
                    profile_snapshot=a.get("profile"),
                    detail=a.get("detail", ""),
                    amount=float(a.get("amount", 0) or 0),
                    created_at=_parse_dt(a.get("time")),
                ))
                n += 1
            print(f"已迁移 {n} 条审计日志")

    # 8. 迁移账号偏好记忆（data/memory/*.md → user_preferences，按 customer_id 归档）
    #     需要用户名 → customer_id 映射，单独开一个事务
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User))
        id_map = {u.username: u.customer_id for u in result.scalars().all()}

    memory_dir = DATA_DIR / "memory"
    n = 0
    if memory_dir.is_dir():
        for md in memory_dir.glob("*.md"):
            actor = md.stem
            customer_id = id_map.get(actor)
            if not customer_id:
                continue
            for line in md.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("- "):
                    continue
                content = line[2:].strip()[:512]
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        session.add(UserPreference(
                            customer_id=customer_id, content=content
                        ))
                n += 1
    print(f"已迁移 {n} 条偏好记忆")

    print("迁移完成！")


if __name__ == "__main__":
    asyncio.run(migrate())
