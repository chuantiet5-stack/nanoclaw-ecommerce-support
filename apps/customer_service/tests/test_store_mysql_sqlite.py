"""SQLite 全链路集成测试（生产落地 P0-1.1）。

不依赖 MySQL：用 sqlite（aiosqlite）跑通完整链路，验证形状兼容承诺——

    data/*.json --(migrations/migrate_json_to_mysql)--> sqlite 库
    --(db.repository)--> (store_mysql 委托) --> 与 JSON 模式完全一致的数据形状

所有数据库操作经 db.sync_bridge.run_async 执行（与生产 mysql 模式同一调用路径），
并打开 store / refund_policy / auth_service 的 DATA_BACKEND="mysql" 门控，
直接测真实生产路径：store 门控 → store_mysql → run_async → repository → sqlite。
"""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import repository
from db.models import Base
from db.sync_bridge import run_async

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def _load_json(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mysql_mode(tmp_path_factory):
    """临时 sqlite 库 + 迁移种子数据 + 打开 mysql 门控（module 级共享）。"""
    db_file = tmp_path_factory.mktemp("sqlite_itest") / "itest.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file.as_posix()}", echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 建表（经桥接循环执行，与后续所有操作同事件循环，避免跨循环连接）
    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    run_async(_create_all())

    # 补丁：repository 与迁移脚本的会话工厂都指向 sqlite
    original_session = repository.AsyncSessionLocal
    repository.AsyncSessionLocal = session_factory

    # 加载迁移脚本（真实迁移逻辑，非复制）并执行：JSON → sqlite
    spec = importlib.util.spec_from_file_location(
        "migrate_json_to_mysql_itest",
        ROOT / "migrations" / "migrate_json_to_mysql.py",
    )
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    mig.AsyncSessionLocal = session_factory
    run_async(mig.migrate())

    # 打开 mysql 门控（store / refund_policy / auth_service）
    from agent.tools.ecom import refund_policy, store
    from security import auth_service
    originals = (
        (store, store.DATA_BACKEND),
        (refund_policy, refund_policy.DATA_BACKEND),
        (auth_service, auth_service.DATA_BACKEND),
    )
    store.DATA_BACKEND = "mysql"
    refund_policy.DATA_BACKEND = "mysql"
    auth_service.DATA_BACKEND = "mysql"

    yield

    # 还原补丁（不影响其他测试模块的 json 模式）
    repository.AsyncSessionLocal = original_session
    for mod, val in originals:
        mod.DATA_BACKEND = val
    run_async(engine.dispose())


# =====================================================================
# 读取形状：mysql 模式返回值与 data/*.json 完全一致（业务工具无感知的承诺）
# =====================================================================

class TestReadShapes:

    def test_users(self, mysql_mode):
        from agent.tools.ecom import store
        users = store.load_users()
        assert users["__default__"] == "han"
        for u in _load_json("users.json")["users"]:
            m = users[u["username"]]
            for k in ("customer_id", "name", "level", "points", "role"):
                assert m[k] == u.get(k), f"{u['username']}.{k} 不一致"

    def test_get_user(self, mysql_mode):
        from agent.tools.ecom import store
        user = store.get_user("han")
        assert user is not None and user["customer_id"] == "C001"
        assert store.get_user("nobody-xyz") is None

    def test_goods_full_shape(self, mysql_mode):
        from agent.tools.ecom import store
        goods = store.load_goods()
        expected = _load_json("goods.json")
        assert len(goods) == len(expected)
        by_sku = {g["sku"]: g for g in goods}
        for e in expected:
            g = by_sku[e["sku"]]
            assert g["name"] == e["name"]
            assert g["category"] == e["category"]
            assert g["price"] == e["price"]
            assert g["highlights"] == e.get("highlights", "")
            assert g["description"] == e.get("description", "")
            assert g["stock"] == e["stock"]
            assert g["sizes"] == e.get("sizes")
            assert g["scenes"] == e.get("scenes")
            assert g["link"] == e.get("link", "")

    def test_orders_full_shape(self, mysql_mode):
        from agent.tools.ecom import store
        orders = store.load_orders()
        expected = _load_json("orders.json")
        assert len(orders) == len(expected)
        by_id = {o["order_id"]: o for o in orders}
        for e in expected:
            o = by_id[e["order_id"]]
            assert o["customer_id"] == e["customer_id"]
            assert o["status"] == e["status"]
            assert o["total"] == e["total"]
            assert o["items"] == e["items"]
            assert o["receiver"] == e["receiver"]
            assert o["created_at"] == e["created_at"]
            assert o.get("paid_at", "") == e.get("paid_at", "")
            assert o.get("signed_at", "") == e.get("signed_at", "")

    def test_find_order(self, mysql_mode):
        from agent.tools.ecom import store
        first = _load_json("orders.json")[0]
        o = store.find_order(first["order_id"])
        assert o is not None and o["order_id"] == first["order_id"]
        assert store.find_order("SO-not-exist") is None

    def test_logistics(self, mysql_mode):
        from agent.tools.ecom import store
        assert store.load_logistics() == _load_json("logistics.json")

    def test_tickets_full_shape(self, mysql_mode):
        from agent.tools.ecom import store
        tickets = store.load_tickets()
        expected = _load_json("tickets.json")
        assert len(tickets) == len(expected)
        by_id = {t["ticket_id"]: t for t in tickets}
        for e in expected:
            t = by_id[e["ticket_id"]]
            assert t["type"] == e["type"]
            assert t["customer_id"] == e["customer_id"]
            assert t["description"] == e["description"]
            assert t["status"] == e["status"]
            assert t["priority"] == e["priority"]
            assert t["order_id"] == e["order_id"]
            assert t["records"] == (e["records"] or [])
            assert t["created_at"] == (e["created_at"] or "")
            assert t.get("updated_at", "") == (e["updated_at"] or "")

    def test_memory_matches_md(self, mysql_mode):
        from agent.tools.ecom import store
        md = DATA_DIR / "memory" / "han.md"
        lines = [
            l[2:].strip()
            for l in md.read_text(encoding="utf-8").splitlines()
            if l.strip().startswith("- ")
        ]
        assert store.load_memory("han") == lines
        assert store.load_memory("nobody-xyz") == []


# =====================================================================
# 写入回环：save_* 落库后再读，数据一致（含 JSON 模式特有的字段不丢失）
# =====================================================================

class TestWriteRoundTrips:

    def test_save_orders_address(self, mysql_mode):
        from agent.tools.ecom import store
        orders = store.load_orders()
        target = orders[0]
        old_addr = target["receiver"]["address"]
        target["receiver"]["address"] = "集成测试新地址 88 号"
        store.save_orders(orders)

        got = store.find_order(target["order_id"])
        assert got["receiver"]["address"] == "集成测试新地址 88 号"
        assert got["receiver"]["name"] == target["receiver"]["name"]
        # 其他字段不丢（items/时间戳完整回读）
        assert got["items"] == target["items"]
        assert got["created_at"] == target["created_at"]

        # 还原（保持 module 级种子数据干净）
        target["receiver"]["address"] = old_addr
        store.save_orders(orders)

    def test_save_tickets_status(self, mysql_mode):
        from agent.tools.ecom import store
        tickets = store.load_tickets()
        target = tickets[0]
        old_status = target["status"]
        target["status"] = "已完成"
        target.setdefault("records", []).append({
            "time": "2026-09-07 12:00:00", "note": "集成测试追加记录",
        })
        store.save_tickets(tickets)

        reloaded = {t["ticket_id"]: t for t in store.load_tickets()}
        got = reloaded[target["ticket_id"]]
        assert got["status"] == "已完成"
        assert got["records"][-1]["note"] == "集成测试追加记录"

        # 还原
        target["status"] = old_status
        target["records"] = target["records"][:-1]
        store.save_tickets(tickets)

    def test_append_memory(self, mysql_mode):
        from agent.tools.ecom import store
        before = len(store.load_memory("han"))
        store.append_memory("han", "喜欢深色装备")
        after = store.load_memory("han")
        assert len(after) == before + 1
        today = datetime.now().strftime("%Y-%m-%d")
        assert after[-1] == f"喜欢深色装备（{today} 记录）"

    def test_new_ticket_id(self, mysql_mode):
        from agent.tools.ecom import store
        existing = {t["ticket_id"] for t in store.load_tickets()}
        tid = store.new_ticket_id()
        assert tid.startswith("TK")
        assert tid not in existing


# =====================================================================
# 鉴权与退款策略的 mysql 分支（auth_service / refund_policy 门控）
# =====================================================================

class TestMysqlBranches:

    def test_authenticate_mysql(self, mysql_mode):
        """迁移后的 bcrypt 哈希可直接校验（防双重哈希回归）。"""
        from security import auth_service
        user = auth_service.authenticate("han", "123456")
        assert user is not None
        assert user["customer_id"] == "C001"
        assert user["role"] == "user"
        assert auth_service.authenticate("han", "wrong-password") is None
        assert auth_service.authenticate("ghost-user", "123456") is None

    def test_refund_records_shape(self, mysql_mode):
        from agent.tools.ecom import refund_policy as rp
        records = rp.load_refund_records()
        # refund_records.json 可能尚未生成（JSON 模式首次退款才创建），
        # 与 JSON 模式的 _load_json(..., []) 行为一致：缺文件 → 空列表
        refund_file = DATA_DIR / "refund_records.json"
        expected = _load_json("refund_records.json") if refund_file.exists() else []
        assert len(records) == len(expected)
        for r in records:
            assert {"refund_no", "order_id", "customer_id", "amount",
                    "reason", "decided_by", "created_at"} <= set(r)

    def test_refund_append_roundtrip(self, mysql_mode):
        from agent.tools.ecom import refund_policy as rp
        rp.save_refund_record({
            "refund_no": "R-ITEST-0001",
            "order_id": "SO20260901001",
            "customer_id": "C001",
            "amount": 9.9,
            "reason": "集成测试退款",
            "decided_by": "auto",
            "rule": "R1",
            "created_at": "2026-09-07 12:00:00",
        })
        assert any(
            r["refund_no"] == "R-ITEST-0001" for r in rp.load_refund_records()
        )

        rp.append_audit({
            "time": "2026-09-07 12:00:00",
            "order_id": "SO20260901001",
            "customer_id": "C001",
            "action": "auto_approve",
            "reason": "集成测试审计",
            "rule": "R1",
            "detail": "detail-text",
            "amount": 9.9,
            "profile": {"level": "黄金会员"},
        })
        # 审计日志无独立读接口（仅管理端 mysql 聚合用），验证不抛异常即可
