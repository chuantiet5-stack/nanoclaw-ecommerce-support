"""SQLite 数据访问层。

独立服务使用独立的 SQLite 数据库文件（data/merchant_mock.db），
首次启动自动建表并写入种子数据，不依赖网络、不依赖 V4 的任何代码。
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import config

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    path = Path(config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    """获取全局连接（懒初始化）。"""
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                _conn = _connect()
    return _conn


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    """执行查询，返回 dict 列表。"""
    with _lock:
        rows = get_conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: tuple | list = ()) -> dict | None:
    """执行查询，返回首行 dict 或 None。"""
    with _lock:
        row = get_conn().execute(sql, params).fetchone()
        return dict(row) if row else None


def execute(sql: str, params: tuple | list = ()) -> int:
    """执行写语句，返回 lastrowid。"""
    with _lock:
        cur = get_conn().execute(sql, params)
        get_conn().commit()
        return cur.lastrowid


def executescript(sql: str) -> None:
    with _lock:
        get_conn().executescript(sql)
        get_conn().commit()


def truncate_all() -> None:
    """清空全部业务表数据（保留表结构），用于 --reseed 前重置演示状态。"""
    with _lock:
        conn = get_conn()
        names = [
            r["name"] for r in query(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")
        ]
        conn.execute("PRAGMA foreign_keys = OFF")
        for name in names:
            conn.execute(f"DELETE FROM {name}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()


@contextmanager
def transaction():
    """简单事务上下文（demo 规模下由全局锁串行化保证一致性）。"""
    with _lock:
        conn = get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ---------- JSON 字段编解码 ----------

def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


# ---------- 表结构 ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS staffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    merchant_id TEXT NOT NULL DEFAULT 'M001',
    status TEXT NOT NULL DEFAULT '在职',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT '普通会员',
    points INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    token TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    merchant_id TEXT NOT NULL DEFAULT 'M001',
    scope TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    price REAL NOT NULL DEFAULT 0,
    original_price REAL NOT NULL DEFAULT 0,
    highlights TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    sizes TEXT NOT NULL DEFAULT '{}',
    colors TEXT NOT NULL DEFAULT '[]',
    scenes TEXT NOT NULL DEFAULT '[]',
    link TEXT NOT NULL DEFAULT '',
    stock INTEGER NOT NULL DEFAULT 0,
    locked_stock INTEGER NOT NULL DEFAULT 0,
    safety_stock INTEGER NOT NULL DEFAULT 10,
    sales INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '在售',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    action TEXT NOT NULL,
    change_qty INTEGER NOT NULL DEFAULT 0,
    before_stock INTEGER NOT NULL DEFAULT 0,
    after_stock INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    prev_status TEXT NOT NULL DEFAULT '',
    items TEXT NOT NULL DEFAULT '[]',
    goods_amount REAL NOT NULL DEFAULT 0,
    discount REAL NOT NULL DEFAULT 0,
    freight REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    receiver_name TEXT NOT NULL DEFAULT '',
    receiver_phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    stock_locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    shipped_at TEXT,
    signed_at TEXT,
    completed_at TEXT,
    cancel_reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logistics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    carrier TEXT NOT NULL DEFAULT '',
    tracking_no TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '待发货',
    current_location TEXT NOT NULL DEFAULT '',
    traces TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS after_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    after_sale_id TEXT UNIQUE NOT NULL,
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待审核',
    reason TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    refund_id TEXT NOT NULL DEFAULT '',
    admin_remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS after_sale_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    after_sale_id TEXT NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    refund_id TEXT UNIQUE NOT NULL,
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    after_sale_id TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    review_type TEXT NOT NULL DEFAULT '',
    review_result TEXT NOT NULL DEFAULT '',
    risk_result TEXT NOT NULL DEFAULT '',
    rule_hit TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '待审核',
    refunded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refund_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    refund_id TEXT NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT UNIQUE NOT NULL,
    customer_id TEXT NOT NULL,
    order_id TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待处理',
    priority INTEGER NOT NULL DEFAULT 2,
    priority_label TEXT NOT NULL DEFAULT '普通',
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    assigned_to TEXT NOT NULL DEFAULT '',
    records TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS op_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fault_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db(force_reseed: bool = False) -> None:
    """建表；库为空（或强制）时写入种子数据。"""
    from mock_merchant import seed

    if force_reseed:
        # 清空全部业务数据后重建，保证回到干净的演示初始状态
        truncate_all()
    executescript(SCHEMA)
    row = query_one("SELECT COUNT(*) AS n FROM products")
    if force_reseed or not row or row["n"] == 0:
        seed.run()
