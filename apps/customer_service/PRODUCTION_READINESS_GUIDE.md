# NanoClaw (V3) 生产落地详细解决方案

> 本文档基于对 V3 项目的完整代码审查，按 **P0（不补不能上线）→ P1（严重影响稳定性）→ P2（体验与可维护性）** 三级优先级，给出每一项的具体改造步骤、代码示例和验证方法。
>
> 预计总工期：6-8 周（1 名全栈 + 1 名后端）。

---

## 目录

- [一、P0 — 必须立即修复](#一p0--必须立即修复)
  - [1.1 数据层重构：JSON 文件 → MySQL + Redis](#11-数据层重构json-文件--mysql--redis)
  - [1.2 安全加固：密钥、鉴权、沙箱](#12-安全加固密钥鉴权沙箱)
  - [1.3 并发架构：Gateway 串行 → 并发消费](#13-并发架构gateway-串行--并发消费)
- [二、P1 — 严重影响生产稳定性](#二p1--严重影响生产稳定性)
  - [2.1 Agent 实例 LRU 淘汰](#21-agent-实例-lru-淘汰)
  - [2.2 LLM 调用：重试 + 超时 + 限流](#22-llm-调用重试--超时--限流)
  - [2.3 日志体系改造](#23-日志体系改造)
  - [2.4 依赖精简与版本锁定](#24-依赖精简与版本锁定)
- [三、P2 — 体验与可维护性](#三p2--体验与可维护性)
  - [3.1 测试体系建设](#31-测试体系建设)
  - [3.2 转人工闭环](#32-转人工闭环)
  - [3.3 RAG 知识库](#33-rag-知识库)
  - [3.4 Docker 化部署](#34-docker-化部署)
  - [3.5 前端工程化](#35-前端工程化)
- [四、落地路线图（周计划）](#四落地路线图周计划)
- [五、上线前验证清单](#五上线前验证清单)

---

# 一、P0 — 必须立即修复

## 1.1 数据层重构：JSON 文件 → MySQL + Redis

### 1.1.1 问题分析

当前 `agent/tools/ecom/store.py` 全部使用 `data/*.json` 文件存储：

```python
# 当前实现（有并发风险）
def load_orders() -> list:
    return _load_json("orders.json", [])       # 读

def save_orders(orders: list) -> None:
    _save_json("orders.json", orders)           # 整体覆盖写
```

**三个致命问题**：

| 问题 | 场景 | 后果 |
|------|------|------|
| 并发写丢失 | 用户A改地址、用户B同时退款，都执行 load→modify→save | 后写入者覆盖先写入者的修改 |
| 写操作非原子 | `path.write_text()` 中途进程被 kill | orders.json 变成半截 JSON，整个系统瘫痪 |
| 数据是模拟的 | 商品/订单/物流都是硬编码 JSON | 无法对接真实业务 |

### 1.1.2 目标架构

```
┌─────────────────────────────────────────────┐
│              Agent Tools 层                  │
│  (SearchGoods / QueryOrder / UpdateAddress) │
└──────────────┬──────────────────────────────┘
               │ 调用
┌──────────────▼──────────────────────────────┐
│         EcomRepository 抽象接口层             │
│  (定义 CRUD 接口，与具体存储解耦)             │
└──────┬───────────────────┬──────────────────┘
       │                   │
┌──────▼──────┐    ┌───────▼───────┐
│   MySQL     │    │    Redis      │
│ (业务数据)   │    │ (会话/缓存)    │
│ orders      │    │ session:xxx   │
│ goods       │    │ rate_limit    │
│ tickets     │    │ lock:order    │
│ users       │    │               │
└─────────────┘    └───────────────┘
```

### 1.1.3 具体步骤

#### 步骤 1：安装依赖

```bash
pip install sqlalchemy==2.0.35 aiomysql==0.2.0 redis==5.2.1 python-dotenv==1.1.0
```

#### 步骤 2：创建数据库表结构

新建 `migrations/001_init.sql`：

```sql
-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id   VARCHAR(64)  NOT NULL UNIQUE COMMENT '顾客ID',
    username      VARCHAR(64)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL COMMENT 'bcrypt哈希',
    name          VARCHAR(64)  NOT NULL,
    phone         VARCHAR(20)  DEFAULT '',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 商品表
CREATE TABLE IF NOT EXISTS goods (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    goods_id    VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(255) NOT NULL,
    category    VARCHAR(64)  DEFAULT '',
    price       DECIMAL(10,2) NOT NULL,
    stock       INT          DEFAULT 0,
    specs       JSON         DEFAULT NULL COMMENT '规格JSON',
    description TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 订单表
CREATE TABLE IF NOT EXISTS orders (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id      VARCHAR(64)  NOT NULL UNIQUE,
    customer_id   VARCHAR(64)  NOT NULL,
    status        VARCHAR(32)  NOT NULL COMMENT '待付款/已付款/已发货/已签收/已完成/已取消',
    total         DECIMAL(10,2) NOT NULL,
    receiver_name VARCHAR(64)  DEFAULT '',
    receiver_phone VARCHAR(20) DEFAULT '',
    address       VARCHAR(512) DEFAULT '',
    items         JSON         DEFAULT NULL COMMENT '商品明细JSON',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    paid_at       DATETIME     NULL,
    shipped_at    DATETIME     NULL,
    signed_at     DATETIME     NULL,
    INDEX idx_customer (customer_id),
    INDEX idx_status (status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 物流表
CREATE TABLE IF NOT EXISTS logistics (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id    VARCHAR(64)  NOT NULL,
    carrier     VARCHAR(64)  DEFAULT '' COMMENT '快递公司',
    tracking_no VARCHAR(64)  DEFAULT '',
    status      VARCHAR(32)  DEFAULT '待发货',
    traces      JSON         DEFAULT NULL COMMENT '物流轨迹JSON',
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 工单表
CREATE TABLE IF NOT EXISTS tickets (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticket_id     VARCHAR(64)  NOT NULL UNIQUE,
    customer_id   VARCHAR(64)  NOT NULL,
    order_id      VARCHAR(64)  DEFAULT '',
    type          VARCHAR(32)  NOT NULL COMMENT '退款/换货/投诉/咨询',
    status        VARCHAR(32)  NOT NULL DEFAULT '待处理' COMMENT '待处理/处理中/已完成/已关闭',
    priority      TINYINT      DEFAULT 1 COMMENT '1普通 2紧急',
    summary       VARCHAR(512) DEFAULT '',
    assigned_to   VARCHAR(64)  DEFAULT '' COMMENT '处理人工号',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_customer (customer_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 退款记录表
CREATE TABLE IF NOT EXISTS refund_records (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    refund_id     VARCHAR(64)  NOT NULL UNIQUE,
    order_id      VARCHAR(64)  NOT NULL,
    customer_id   VARCHAR(64)  NOT NULL,
    amount        DECIMAL(10,2) NOT NULL,
    reason        VARCHAR(255) DEFAULT '',
    decided_by    VARCHAR(16)  NOT NULL COMMENT 'auto/manual',
    decision      VARCHAR(16)  NOT NULL COMMENT 'approve/reject',
    rule_hit      VARCHAR(16)  DEFAULT '' COMMENT '命中规则R0-R8',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer (customer_id),
    INDEX idx_order (order_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 退款审计日志表
CREATE TABLE IF NOT EXISTS refund_audit_log (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    refund_id     VARCHAR(64)  NOT NULL,
    customer_id   VARCHAR(64)  NOT NULL,
    order_id      VARCHAR(64)  NOT NULL,
    action        VARCHAR(16)  NOT NULL,
    reason        VARCHAR(512) DEFAULT '',
    rule_hit      VARCHAR(16)  DEFAULT '',
    profile_snapshot JSON      DEFAULT NULL COMMENT '决策时用户画像快照',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_refund (refund_id),
    INDEX idx_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用户偏好记忆表
CREATE TABLE IF NOT EXISTS user_preferences (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id VARCHAR(64)  NOT NULL,
    content     VARCHAR(512) NOT NULL,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

执行建表：

```bash
mysql -u root -p ecom_db < migrations/001_init.sql
```

#### 步骤 3：创建数据库连接管理

新建 `db/database.py`：

```python
"""数据库连接管理（SQLAlchemy 异步 + Redis）"""

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
import redis.asyncio as aioredis

# 从环境变量读取，禁止硬编码
DB_URL = os.getenv("DB_URL", "mysql+aiomysql://user:pass@localhost:3306/ecom_db")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# 异步引擎（连接池）
engine = create_async_engine(
    DB_URL,
    pool_size=10,            # 连接池大小
    max_overflow=20,         # 超出池大小的最大临时连接
    pool_timeout=30,         # 获取连接超时
    pool_recycle=1800,       # 连接回收时间（秒），防止MySQL 8小时断连
    echo=False,              # 生产环境关闭SQL日志
)

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

# Redis 客户端
redis_client = aioredis.from_url(
    REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50,
)


async def get_db() -> AsyncSession:
    """依赖注入用：获取数据库会话"""
    async with AsyncSessionLocal() as session:
        yield session
```

#### 步骤 4：定义 ORM 模型

新建 `db/models.py`：

```python
"""SQLAlchemy ORM 模型"""

from sqlalchemy import Column, BigInteger, String, Integer, Float, DateTime, Text, JSON, func
from db.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    customer_id = Column(String(64), unique=True, nullable=False)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(64), nullable=False)
    phone = Column(String(20), default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Goods(Base):
    __tablename__ = "goods"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    goods_id = Column(String(64), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    category = Column(String(64), default="")
    price = Column(Float, nullable=False)
    stock = Column(Integer, default=0)
    specs = Column(JSON, default=None)
    description = Column(Text, default="")


class Order(Base):
    __tablename__ = "orders"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_id = Column(String(64), unique=True, nullable=False)
    customer_id = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    total = Column(Float, nullable=False)
    receiver_name = Column(String(64), default="")
    receiver_phone = Column(String(20), default="")
    address = Column(String(512), default="")
    items = Column(JSON, default=None)
    created_at = Column(DateTime, server_default=func.now())
    paid_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    signed_at = Column(DateTime, nullable=True)


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id = Column(String(64), unique=True, nullable=False)
    customer_id = Column(String(64), nullable=False, index=True)
    order_id = Column(String(64), default="")
    type = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="待处理")
    priority = Column(Integer, default=1)
    summary = Column(String(512), default="")
    assigned_to = Column(String(64), default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class RefundRecord(Base):
    __tablename__ = "refund_records"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    refund_id = Column(String(64), unique=True, nullable=False)
    order_id = Column(String(64), nullable=False, index=True)
    customer_id = Column(String(64), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    reason = Column(String(255), default="")
    decided_by = Column(String(16), nullable=False)
    decision = Column(String(16), nullable=False)
    rule_hit = Column(String(16), default="")
    created_at = Column(DateTime, server_default=func.now())


class UserPreference(Base):
    __tablename__ = "user_preferences"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    customer_id = Column(String(64), nullable=False, index=True)
    content = Column(String(512), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
```

#### 步骤 5：实现 Repository 层（替换 store.py）

新建 `db/repository.py`：

```python
"""数据访问层：所有业务工具通过此层读写数据库，替代原 store.py"""

from typing import Optional
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import User, Goods, Order, Ticket, RefundRecord, UserPreference
from db.database import AsyncSessionLocal


# ========== 会话管理 ==========

def get_session() -> AsyncSession:
    """获取一个新的数据库会话（使用后需自行 close）"""
    return AsyncSessionLocal()


# ========== 用户 ==========

async def get_user_by_username(username: str) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()


async def get_user_by_customer_id(customer_id: str) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.customer_id == customer_id))
        return result.scalar_one_or_none()


# ========== 商品 ==========

async def search_goods(keyword: str = "", category: str = "", limit: int = 20) -> list[dict]:
    async with AsyncSessionLocal() as session:
        stmt = select(Goods)
        if keyword:
            stmt = stmt.where(Goods.name.like(f"%{keyword}%"))
        if category:
            stmt = stmt.where(Goods.category == category)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return [_goods_to_dict(g) for g in result.scalars().all()]


async def get_goods_by_id(goods_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Goods).where(Goods.goods_id == goods_id))
        g = result.scalar_one_or_none()
        return _goods_to_dict(g) if g else None


def _goods_to_dict(g: Goods) -> dict:
    return {
        "goods_id": g.goods_id,
        "name": g.name,
        "category": g.category,
        "price": g.price,
        "stock": g.stock,
        "specs": g.specs,
        "description": g.description,
    }


# ========== 订单 ==========

async def find_order(order_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.order_id == order_id))
        o = result.scalar_one_or_none()
        return _order_to_dict(o) if o else None


async def list_orders_by_customer(customer_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order).where(Order.customer_id == customer_id).order_by(Order.created_at.desc())
        )
        return [_order_to_dict(o) for o in result.scalars().all()]


async def update_order_address(order_id: str, new_address: str) -> bool:
    """更新订单地址（使用事务，原子操作）"""
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
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        t = result.scalar_one_or_none()
        if not t:
            return None
        return {
            "ticket_id": t.ticket_id,
            "status": t.status,
            "type": t.type,
            "summary": t.summary,
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
        }


# ========== 退款 ==========

async def count_refunds_by_customer(customer_id: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(RefundRecord.id)).where(RefundRecord.customer_id == customer_id)
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
    async with AsyncSessionLocal() as session:
        async with session.begin():
            r = RefundRecord(**record)
            session.add(r)


# ========== 偏好记忆 ==========

async def list_preferences(customer_id: str) -> list[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserPreference.content).where(UserPreference.customer_id == customer_id)
        )
        return [row[0] for row in result.all()]


async def append_preference(customer_id: str, content: str) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            p = UserPreference(customer_id=customer_id, content=content)
            session.add(p)
```

#### 步骤 6：改造电商工具，从 store.py 切换到 repository

以 `update_address.py` 为例，改造后：

```python
"""修改地址工具（数据库版）"""

from agent.tools.base import Tool
from db import repository
from agent.tools.ecom.store import mask_address, check_login, check_order_owner


class UpdateAddressTool(Tool):
    def __init__(self, actor=None, logged_in=False):
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self): return "update_address"

    @property
    def description(self):
        return ("修改订单的收货地址（需登录）。仅适用于未签收订单。"
                "写操作，必须先向顾客复述并获得确认后调用。"
                "输入：order_id、new_address。")

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单号"},
                "new_address": {"type": "string", "description": "新收货地址"},
            },
            "required": ["order_id", "new_address"],
        }

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        user = await repository.get_user_by_username(self._actor)
        if user is None:
            return "账号信息异常，请重新登录。"

        order_id = str(kwargs.get("order_id") or "").strip()
        new_address = str(kwargs.get("new_address") or "").strip()

        if not order_id or not new_address:
            return "请提供订单号和新的收货地址。"

        order = await repository.find_order(order_id)
        if order is None:
            return f"未找到订单 {order_id}，请核对订单号。"

        denied = check_order_owner(order, user.customer_id)
        if denied:
            return denied

        if order["status"] in ("已签收", "已完成"):
            return f"订单 {order_id} 已{order['status']}，无法修改收货地址。"

        # 数据库事务更新，原子安全
        success = await repository.update_order_address(order_id, new_address)
        if not success:
            return "修改失败，请稍后重试。"

        return (
            f"订单 {order_id} 的收货地址已修改成功：\n"
            f"- 新地址：{mask_address(new_address)}\n"
            "后续物流信息将按新地址派送。"
        )
```

> **注意**：`store.py` 中的脱敏函数（`mask_name`/`mask_phone`/`mask_address`）、业务常量（`RETURN_WINDOW_DAYS`）、校验函数（`check_login`/`check_order_owner`）保留不动，只把数据读写部分替换为 repository 调用。

#### 步骤 7：会话历史迁移到 Redis

新建 `session/redis_manager.py`，替代原 `session/manager.py` 的文件存储：

```python
"""基于 Redis 的会话管理器（替代 JSONL 文件存储）"""

import json
from datetime import datetime, timedelta
from db.database import redis_client


class RedisSessionManager:
    SESSION_TTL = timedelta(days=7)  # 会话7天过期

    def __init__(self, prefix: str = "session:"):
        self.prefix = prefix

    def _key(self, session_key: str) -> str:
        return f"{self.prefix}{session_key}"

    async def save_message(self, session_key: str, message: dict) -> None:
        """保存单条消息到 Redis List"""
        message["timestamp"] = datetime.now().isoformat()
        key = self._key(session_key)
        # RPUSH 追加到列表尾部
        await redis_client.rpush(key, json.dumps(message, ensure_ascii=False))
        # 每次写入刷新过期时间
        await redis_client.expire(key, int(self.SESSION_TTL.total_seconds()))

    async def get_history(self, session_key: str) -> list[dict]:
        """加载全部历史消息"""
        key = self._key(session_key)
        raw_list = await redis_client.lrange(key, 0, -1)
        history = []
        for raw in raw_list:
            try:
                msg = json.loads(raw)
                msg.pop("timestamp", None)
                history.append(msg)
            except json.JSONDecodeError:
                continue
        return history

    async def clear(self, session_key: str) -> None:
        await redis_client.delete(self._key(session_key))

    async def list_sessions(self) -> list[str]:
        """列出所有活跃会话 key"""
        keys = await redis_client.keys(f"{self.prefix}*")
        return [k.removeprefix(self.prefix) for k in keys]
```

#### 步骤 8：数据迁移脚本

新建 `migrations/migrate_json_to_mysql.py`，把现有 JSON 数据导入 MySQL：

```python
"""将 data/*.json 模拟数据导入 MySQL（一次性迁移脚本）"""

import json
from pathlib import Path
from db.database import AsyncSessionLocal
from db.models import User, Goods, Order, Ticket
import bcrypt

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


async def migrate():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. 迁移用户
            users_data = json.loads((DATA_DIR / "users.json").read_text(encoding="utf-8"))
            for u in users_data.get("users", []):
                password_hash = bcrypt.hashpw(u["password"].encode(), bcrypt.gensalt()).decode()
                session.add(User(
                    customer_id=u["customer_id"],
                    username=u["username"],
                    password_hash=password_hash,
                    name=u["name"],
                    phone=u.get("phone", ""),
                ))
            print(f"已迁移 {len(users_data.get('users', []))} 个用户")

            # 2. 迁移商品
            goods_list = json.loads((DATA_DIR / "goods.json").read_text(encoding="utf-8"))
            for g in goods_list:
                session.add(Goods(
                    goods_id=g["goods_id"],
                    name=g["name"],
                    category=g.get("category", ""),
                    price=g["price"],
                    stock=g.get("stock", 0),
                    specs=g.get("specs"),
                    description=g.get("description", ""),
                ))
            print(f"已迁移 {len(goods_list)} 个商品")

            # 3. 迁移订单
            orders_list = json.loads((DATA_DIR / "orders.json").read_text(encoding="utf-8"))
            for o in orders_list:
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
                ))
            print(f"已迁移 {len(orders_list)} 个订单")

    print("迁移完成！")


if __name__ == "__main__":
    import asyncio
    asyncio.run(migrate())
```

执行迁移：

```bash
python migrations/migrate_json_to_mysql.py
```

### 1.1.4 验证方法

1. **并发写测试**：用 `asyncio.gather` 同时发起 10 个 `update_order_address` 调用，验证最终地址正确且无数据丢失
2. **崩溃恢复测试**：在写操作中途 kill 进程，重启后检查数据库数据完整性
3. **性能测试**：用 `wrk` 或 `locust` 压测，对比 JSON 文件版和 MySQL 版的 QPS

---

## 1.2 安全加固：密钥、鉴权、沙箱

### 1.2.1 问题清单

| 问题 | 位置 | 风险等级 |
|------|------|---------|
| API Key / 飞书 / QQ 密钥明文写在 config.json | `config.json` | 🔴 极高 |
| 用户密码明文存储 | `data/users.json` | 🔴 极高 |
| Web 登录仅内存字典，无 Token | `channels/web.py` `_logins` | 🔴 高 |
| 管理端 API 完全无鉴权 | `channels/web.py` `/api/*` | 🔴 高 |
| WebSocket 无鉴权 | `channels/web.py` `/ws` | 🟡 中 |
| ExecTool 可执行任意命令 | `agent/tools/shell.py` | 🔴 极高 |
| 无 HTTPS / CORS 配置 | `channels/web.py` | 🟡 中 |

### 1.2.2 具体步骤

#### 步骤 1：密钥外移到环境变量

新建 `.env` 文件（**加入 .gitignore，绝不提交**）：

```bash
# .env — 生产环境密钥（此文件不提交到 Git）
NANOCLAW_API_KEY=sk-your-real-key
NANOCLAW_BASE_URL=https://api.siliconflow.cn/v1
NANOCLAW_MODEL=zai-org/GLM-5.3

# 数据库
DB_URL=mysql+aiomysql://ecom_user:strong_password@localhost:3306/ecom_db
REDIS_URL=redis://localhost:6379/0

# 飞书
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx

# QQ
QQ_APP_ID=123456
QQ_APP_SECRET=xxx

# JWT 密钥（生产环境用 openssl rand -hex 32 生成）
JWT_SECRET=your-256-bit-secret-here
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Web
WEB_HOST=0.0.0.0
WEB_PORT=8080
```

新建 `.gitignore`：

```
.env
.env.*
config.json
__pycache__/
*.pyc
workspace/sessions/
data/*.json
!data/*.example.json
botpy.log*
```

改造 `config.py`，用 `python-dotenv` 加载环境变量：

```python
"""配置模块（环境变量版，替代 config.json）"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()  # 自动加载 .env 文件


@dataclass
class NanoClawConfig:
    api_key: str = os.getenv("NANOCLAW_API_KEY", "")
    base_url: str = os.getenv("NANOCLAW_BASE_URL", "https://api.siliconflow.cn/v1")
    model: str = os.getenv("NANOCLAW_MODEL", "zai-org/GLM-5.3")
    models: dict = None
    workspace: str = os.getenv("NANOCLAW_WORKSPACE", ".")
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "32"))
    identity_file: str = os.getenv("IDENTITY_FILE", "identity.md")

    # 飞书
    feishu_app_id: str = os.getenv("FEISHU_APP_ID", "")
    feishu_app_secret: str = os.getenv("FEISHU_APP_SECRET", "")

    # QQ
    qq_app_id: str = os.getenv("QQ_APP_ID", "")
    qq_app_secret: str = os.getenv("QQ_APP_SECRET", "")

    # Web
    web_enabled: bool = os.getenv("WEB_ENABLED", "true").lower() == "true"
    web_host: str = os.getenv("WEB_HOST", "0.0.0.0")
    web_port: int = int(os.getenv("WEB_PORT", "8080"))

    # 数据库
    db_url: str = os.getenv("DB_URL", "")
    redis_url: str = os.getenv("REDIS_URL", "")

    # JWT
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

    # MCP
    mcp_servers: dict = None


def load_config() -> NanoClawConfig:
    return NanoClawConfig()
```

#### 步骤 2：密码哈希存储

安装 bcrypt：

```bash
pip install bcrypt==4.3.0
```

新建 `security/password.py`：

```python
"""密码哈希与校验"""

import bcrypt


def hash_password(plain_password: str) -> str:
    """生成 bcrypt 哈希（含 salt）"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码（恒定时间比较，防时序攻击）"""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False
```

#### 步骤 3：JWT 鉴权体系

新建 `security/jwt.py`：

```python
"""JWT Token 生成与校验"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """生成 JWT Access Token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """校验并解析 JWT，失败返回 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
```

新建 `security/deps.py`（FastAPI 依赖注入）：

```python
"""FastAPI 鉴权依赖"""

from fastapi import Header, HTTPException, status, WebSocket
from security.jwt import decode_access_token


async def get_current_user(authorization: str = Header(None)) -> dict:
    """从 Authorization: Bearer <token> 解析当前用户"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
        )
    token = authorization.removeprefix("Bearer ")
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
        )
    return payload


async def get_current_admin(authorization: str = Header(None)) -> dict:
    """管理员鉴权（在用户鉴权基础上检查 role）"""
    user = await get_current_user(authorization)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


async def get_ws_user(websocket: WebSocket) -> Optional[dict]:
    """WebSocket 连接时从 query 参数或 header 取 token"""
    token = websocket.query_params.get("token") or ""
    if not token:
        auth = websocket.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ")
    return decode_access_token(token) if token else None
```

#### 步骤 4：改造 Web 渠道登录与 API 鉴权

修改 `channels/web.py` 的登录处理：

```python
# 替换原 _handle_control 中的 login 逻辑
from security.password import verify_password
from security.jwt import create_access_token
from security.deps import get_current_user, get_current_admin

if msg_type == "login":
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    user = await repository.get_user_by_username(username)
    if user and verify_password(password, user.password_hash):
        # 生成 JWT Token
        token = create_access_token({
            "sub": user.username,
            "customer_id": user.customer_id,
            "role": "user",  # 管理员从数据库 role 字段读取
        })
        self._logins[client_id] = username
        return {
            "type": "login_ok",
            "username": username,
            "name": user.name,
            "token": token,  # 前端保存 token，后续 API 请求带在 header
        }
    return {"type": "login_fail", "message": "账号或密码错误"}
```

给管理端 API 加鉴权装饰器：

```python
# 改造前（完全开放）
@self._app.get("/api/sessions")
async def api_sessions():
    ...

# 改造后（需管理员登录）
@self._app.get("/api/sessions")
async def api_sessions(user: dict = Depends(get_current_admin)):
    ...
```

> 注意：需要在 `_register_routes` 中 `from fastapi import Depends`。

#### 步骤 5：WebSocket 鉴权

修改 `websocket_endpoint`，连接时校验 token：

```python
@self._app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query("")):
    await websocket.accept()

    # 校验 token（游客可连接，但登录用户需有效 token）
    payload = decode_access_token(token) if token else None

    client_id = str(uuid.uuid4())
    if payload:
        # 已登录用户
        guest_id = payload["sub"]
        self._logins[client_id] = payload["sub"]
    else:
        # 游客
        guest_id = f"guest-{client_id[:8]}"
        self._logins[client_id] = None

    self._connections[client_id] = websocket
    # ... 后续逻辑不变
```

#### 步骤 6：ExecTool 沙箱化或下线

**方案 A（推荐）：生产环境直接移除 ExecTool**

修改 `main.py` 的 `build_agent`：

```python
# 在线模式注册工具时，根据环境变量决定是否启用危险工具
ENABLE_SHELL = os.getenv("ENABLE_SHELL_TOOL", "false").lower() == "true"

if ENABLE_SHELL:
    tools.register(ExecTool(config.workspace))
    print("⚠️  警告：已启用 Shell 工具，仅限开发环境使用！")
else:
    print("🔒 Shell 工具已禁用（生产环境安全策略）")
```

**方案 B：命令白名单 + 资源限制**

如果必须保留 Shell 能力，改造 `agent/tools/shell.py`：

```python
import shlex
import asyncio

# 允许的命令白名单
ALLOWED_COMMANDS = {
    "ls", "cat", "head", "tail", "grep", "wc", "pwd", "echo", "date",
    "python", "pip", "git", "npm",
}

class ExecTool(Tool):
    async def execute(self, **kwargs) -> str:
        command = str(kwargs.get("command", ""))
        if not command:
            return "请提供要执行的命令。"

        # 解析命令，检查白名单
        try:
            parts = shlex.split(command)
        except ValueError:
            return "命令格式错误。"

        cmd_base = parts[0].split("/")[-1]  # 取命令名（去掉路径）
        if cmd_base not in ALLOWED_COMMANDS:
            return f"命令 '{cmd_base}' 不在允许列表中。允许的命令：{', '.join(sorted(ALLOWED_COMMANDS))}"

        # 禁止危险操作符
        dangerous = [";", "&&", "||", "|", ">", ">>", "<", "`", "$(", "${", "rm -rf", "sudo"]
        if any(op in command for op in dangerous):
            return "命令包含禁止的操作符，已拦截。"

        # 限时执行（10秒超时）
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = (stdout + stderr).decode("utf-8", errors="replace")
            return output[:2000]  # 截断输出
        except asyncio.TimeoutError:
            proc.kill()
            return "命令执行超时（10秒），已终止。"
```

#### 步骤 7：CORS 与 HTTPS

在 `channels/web.py` 的 `_register_routes` 中添加 CORS 中间件：

```python
from fastapi.middleware.cors import CORSMiddleware

# 允许的来源（生产环境填具体域名，不要用 *）
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8080").split(",")

self._app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
```

HTTPS 通过 Nginx 反向代理实现（见 3.4 节 Docker 部署），不在应用层处理。

### 1.2.3 验证方法

1. 未登录访问 `/api/stats` → 应返回 401
2. 普通用户访问 `/api/sessions` → 应返回 403
3. 错误密码登录 → 返回 login_fail
4. 过期 JWT 访问 API → 返回 401
5. ExecTool 执行 `rm -rf /` → 应被拦截
6. 检查代码仓库中无明文密钥（`git log -p | grep api_key`）

---

## 1.3 并发架构：Gateway 串行 → 并发消费

### 1.3.1 问题分析

当前 `gateway.py` 的 `_process_inbound` 是单循环串行：

```python
async def _process_inbound(self) -> None:
    while True:
        inbound_msg = await self.bus.consume_inbound()  # 取一条
        response = await agent.run(inbound_msg.content)  # 处理（可能耗时30秒）
        await self.bus.publish_outbound(...)             # 发回复
        # 下一条消息必须等上面全部完成
```

**后果**：100 个用户同时发消息，第 100 个用户要等前 99 个全部处理完才能得到响应。

### 1.3.2 改造方案

```
                    ┌──────────────────────┐
                    │   asyncio.Queue      │
                    │   (inbound_queue)    │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  消费循环（快速取消息）│
                    │  每条消息 create_task │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
         ┌─────────┐     ┌─────────┐     ┌─────────┐
         │ Task 1  │     │ Task 2  │     │ Task N  │
         │ agent A │     │ agent B │     │ agent C │
         └────┬────┘     └────┬────┘     └────┬────┘
              │               │               │
              └───────────────┼───────────────┘
                              ▼
                    ┌──────────────────────┐
                    │  信号量限流（如并发20）│
                    └──────────────────────┘
```

### 1.3.3 具体步骤

#### 步骤 1：改造 Gateway 入站处理

修改 `gateway.py`：

```python
import asyncio
from typing import Callable
from bus.queue import MessageBus, InboundMessage, OutboundMessage
from channels.base import Channel
from agent.loop import AgentLoop


class Gateway:
    # 并发控制参数
    MAX_CONCURRENT_AGENTS = 20  # 最大并发 Agent 处理数
    TASK_TIMEOUT = 120          # 单条消息处理超时（秒）

    def __init__(self, bus, channels, agent_factory):
        self.bus = bus
        self.channels = channels
        self.agent_factory = agent_factory
        self._agents: dict[str, AgentLoop] = {}
        self._channel_map: dict[str, Channel] = {}
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_AGENTS)
        self._active_tasks: set[asyncio.Task] = set()

        for channel in channels:
            self._channel_map[channel.name] = channel

    async def _process_inbound(self) -> None:
        """入站消费循环：快速取消息，每条消息创建独立 Task 并发处理"""
        while True:
            try:
                inbound_msg = await self.bus.consume_inbound()
                # 创建独立 Task，不阻塞下一条消息消费
                task = asyncio.create_task(
                    self._handle_message(inbound_msg)
                )
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)

            except asyncio.CancelledError:
                # 取消所有进行中的任务
                for t in self._active_tasks:
                    t.cancel()
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
                break
            except Exception as e:
                print(f"警告: 入站消费循环异常 - {e}")
                continue

    async def _handle_message(self, inbound_msg: InboundMessage) -> None:
        """处理单条消息（带限流 + 超时 + 错误隔离）"""
        session_key = f"{inbound_msg.channel}:{inbound_msg.sender_id}"

        async with self._semaphore:  # 限流
            try:
                # 获取或创建 Agent
                if session_key not in self._agents:
                    agent = self.agent_factory(session_key)
                    self._agents[session_key] = agent
                else:
                    agent = self._agents[session_key]

                # 带超时处理
                response = await asyncio.wait_for(
                    agent.run(inbound_msg.content),
                    timeout=self.TASK_TIMEOUT,
                )

                outbound_msg = OutboundMessage(
                    channel=inbound_msg.channel,
                    chat_id=inbound_msg.chat_id,
                    content=response,
                )
                await self.bus.publish_outbound(outbound_msg)

            except asyncio.TimeoutError:
                error_msg = OutboundMessage(
                    channel=inbound_msg.channel,
                    chat_id=inbound_msg.chat_id,
                    content="抱歉，处理超时，请稍后重试或换个问法。",
                )
                await self.bus.publish_outbound(error_msg)
                print(f"警告: 会话 {session_key} 处理超时")

            except Exception as e:
                error_msg = OutboundMessage(
                    channel=inbound_msg.channel,
                    chat_id=inbound_msg.chat_id,
                    content="抱歉，处理消息时发生了错误，请稍后重试。",
                )
                await self.bus.publish_outbound(error_msg)
                print(f"警告: Agent 处理出错 [{session_key}] - {e}")
```

#### 步骤 2：消息队列持久化（可选，高可用要求时）

如果需要多实例部署或消息不丢失，将 `bus/queue.py` 的 `asyncio.Queue` 替换为 Redis Stream：

```python
"""基于 Redis Stream 的消息总线（替代内存 asyncio.Queue）"""

import json
from db.database import redis_client

INBOUND_STREAM = "nanoclaw:inbound"
OUTBOUND_STREAM = "nanoclaw:outbound"
CONSUMER_GROUP = "gateway-group"


class RedisMessageBus:
    async def publish_inbound(self, msg):
        await redis_client.xadd(INBOUND_STREAM, {
            "channel": msg.channel,
            "sender_id": msg.sender_id,
            "chat_id": msg.chat_id,
            "content": msg.content,
        })

    async def consume_inbound(self):
        # 使用消费者组，支持多实例竞争消费
        result = await redis_client.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=f"gateway-{id(self)}",
            streams={INBOUND_STREAM: ">"},
            count=1,
            block=0,  # 阻塞等待
        )
        # 解析并返回 InboundMessage...
```

### 1.3.4 验证方法

1. 并发测试：同时发 20 条消息，观察总耗时是否约等于单条耗时（而非 20 倍）
2. 超时测试：构造一个会触发 30 次工具调用的请求，验证 120 秒后返回超时提示
3. 错误隔离：一条消息触发 Agent 崩溃，其他消息不受影响

---

# 二、P1 — 严重影响生产稳定性

## 2.1 Agent 实例 LRU 淘汰

### 问题

`Gateway._agents` 字典只增不减，长期运行内存泄漏。

### 解决方案

使用 `collections.OrderedDict` 实现 LRU，超过阈值时淘汰最久未使用的 Agent。

修改 `gateway.py`：

```python
from collections import OrderedDict

class Gateway:
    MAX_AGENTS = 500  # 最多缓存 500 个 Agent 实例

    def __init__(self, ...):
        self._agents: OrderedDict[str, AgentLoop] = OrderedDict()
        ...

    def _get_or_create_agent(self, session_key: str) -> AgentLoop:
        if session_key in self._agents:
            # 移到末尾（最近使用）
            self._agents.move_to_end(session_key)
            return self._agents[session_key]

        # 超过上限，淘汰最久未使用的
        if len(self._agents) >= self.MAX_AGENTS:
            oldest_key, oldest_agent = self._agents.popitem(last=False)
            print(f"[LRU] 淘汰空闲 Agent: {oldest_key}")
            # 可选：将 Agent 的内存历史写回持久化（Redis 已自动处理）

        agent = self.agent_factory(session_key)
        self._agents[session_key] = agent
        return agent
```

在 `_handle_message` 中替换原有的 `if session_key not in self._agents` 逻辑为 `agent = self._get_or_create_agent(session_key)`。

---

## 2.2 LLM 调用：重试 + 超时 + 限流

### 问题

`providers/openai_compat.py` 的 `chat()` 无超时、无重试、无限流。

### 解决方案

#### 步骤 1：添加重试和超时

```bash
pip install tenacity==8.5.0
```

改造 `providers/openai_compat.py`：

```python
import json
from typing import Any
from openai import AsyncOpenAI
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type,
)
from providers.base import LLMProvider, LLMResponse, ToolCallRequest


class OpenAICompatProvider(LLMProvider):
    REQUEST_TIMEOUT = 30  # 秒
    MAX_RETRIES = 3

    def __init__(self, api_key, base_url, model):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.REQUEST_TIMEOUT,  # 超时
            max_retries=0,  # 我们自己用 tenacity 控制重试
        )

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),  # 2s, 4s, 8s
        retry=retry_if_exception_type((
            Exception,  # 网络错误、限流等
        )),
        reraise=True,
    )
    async def _chat_with_retry(self, request_params):
        return await self.client.chat.completions.create(**request_params)

    async def chat(self, messages, tools=None, model=None):
        try:
            request_params = {"model": model or self.model, "messages": messages}
            if tools:
                request_params["tools"] = tools
                request_params["tool_choice"] = "auto"

            response = await self._chat_with_retry(request_params)
            # ... 解析响应（原逻辑不变）

        except Exception as e:
            return LLMResponse(
                content=f"错误: API 调用失败（已重试{self.MAX_RETRIES}次）- {type(e).__name__}",
                finish_reason="error",
            )
```

#### 步骤 2：全局限流（Token Bucket）

新建 `providers/rate_limiter.py`：

```python
"""全局限流器：防止 LLM API 调用超配额"""

import asyncio
import time


class TokenBucket:
    def __init__(self, rate: int, capacity: int):
        """
        rate: 每秒生成的 token 数（QPS）
        capacity: 桶容量（最大突发请求数）
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取一个 token，不够则等待"""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_refill = now

                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                # 计算需要等待多久
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)


# 全局限流器实例（按实际 API 配额调整）
llm_limiter = TokenBucket(rate=10, capacity=20)  # 每秒10次，突发最多20次
```

在 `AgentLoop.run()` 调用 LLM 前加限流：

```python
from providers.rate_limiter import llm_limiter

# 在 loop.py 的 run() 方法中
await llm_limiter.acquire()  # 限流
response = await self.provider.chat(...)
```

---

## 2.3 日志体系改造

### 问题

全项目用 `print()`，无级别、无结构化、无文件输出。

### 解决方案

新建 `utils/logger.py`：

```python
"""结构化日志配置"""

import logging
import json
import sys
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """JSON 格式日志，方便 ELK/Loki 采集"""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 附加异常信息
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        # 附加自定义字段
        if hasattr(record, "session_key"):
            log_entry["session_key"] = record.session_key
        if hasattr(record, "tool_name"):
            log_entry["tool_name"] = record.tool_name
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger(name: str = "nanoclaw", level: str = "INFO") -> logging.Logger:
    """配置并返回 logger"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 控制台输出（人类可读）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(console_handler)

    # 文件输出（JSON 格式，供日志采集）
    import os
    log_dir = os.getenv("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(f"{log_dir}/app.log", encoding="utf-8")
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)

    return logger


# 全局 logger 实例
logger = setup_logger()
```

使用方式（替换所有 print）：

```python
from utils.logger import logger

# 改造前
print(f"[{session_key}] 已注册工具：{tools.list_tools()}")

# 改造后
logger.info("已注册工具", extra={"session_key": session_key, "tools": tools.list_tools()})
logger.warning("Agent 处理出错", extra={"session_key": session_key, "error": str(e)})
logger.error("关键错误", exc_info=True)  # 自动附带堆栈
```

---

## 2.4 依赖精简与版本锁定

### 问题

`requirements.txt` 有 400+ 包，是 conda 环境 dump，包含大量无关依赖。

### 解决方案

生成精简版 `requirements.txt`：

```txt
# ===== 核心依赖 =====
openai==1.93.0
fastapi==0.136.1
uvicorn==0.46.0
python-dotenv==1.1.0
pydantic==2.13.4
httpx==0.28.1

# ===== 数据库 =====
sqlalchemy==2.0.35
aiomysql==0.2.0
redis==5.2.1
bcrypt==4.3.0
PyJWT==2.12.1

# ===== 渠道 SDK =====
qq-botpy==1.2.1
lark-oapi==1.6.1

# ===== MCP =====
mcp==1.27.0
fastmcp==3.2.4

# ===== 工具 =====
beautifulsoup4==4.13.3
tenacity==8.5.0

# ===== 开发与测试 =====
pytest==9.0.2
pytest-asyncio==0.24.0
ruff==0.11.4
```

锁定 Python 版本，新建 `runtime.txt`：

```
python-3.11.9
```

验证依赖完整性：

```bash
# 在干净环境中安装
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py  # 验证能正常启动
```

---

# 三、P2 — 体验与可维护性

## 3.1 测试体系建设

### 步骤 1：目录结构

```
tests/
├── conftest.py              # pytest 配置与 fixture
├── test_refund_policy.py    # 退款规则引擎测试（最关键）
├── test_tools_ecom.py       # 电商工具测试
├── test_agent_loop.py       # Agent 循环测试
├── test_gateway.py          # 网关并发测试
└── test_security.py         # 安全测试
```

### 步骤 2：退款规则引擎测试（最高优先级）

新建 `tests/test_refund_policy.py`：

```python
"""退款风控规则引擎测试 —— 涉及资金，必须全覆盖"""

import pytest
from agent.tools.ecom.refund_policy import classify_refund, RefundDecision


@pytest.fixture
def normal_order():
    return {"order_id": "SO001", "total": 30, "signed_at": "2026-09-05 10:00:00"}


@pytest.fixture
def expensive_order():
    return {"order_id": "SO002", "total": 200, "signed_at": "2026-09-05 10:00:00"}


class TestRefundPolicy:
    def test_r8_auto_approve_normal(self, normal_order):
        """R8: 小额+7天内+无理由+正常账号 → 自动通过"""
        decision = classify_refund(normal_order, "不喜欢", "C001")
        assert decision.action == "auto_approve"
        assert decision.rule == "R8"

    def test_r4_auto_reject_overdue(self):
        """R4: 超过7天无理由期 → 自动拒绝"""
        order = {"order_id": "SO003", "total": 30, "signed_at": "2026-01-01 10:00:00"}
        decision = classify_refund(order, "不喜欢", "C001")
        assert decision.action == "auto_reject"
        assert decision.rule == "R4"

    def test_r5_amount_exceed(self, expensive_order):
        """R5: 金额超过上限 → 转人工"""
        decision = classify_refund(expensive_order, "不喜欢", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R5"

    def test_r3_quality_issue(self, normal_order):
        """R3: 质量问题 → 转人工（需证据）"""
        decision = classify_refund(normal_order, "质量问题，开胶了", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R3"

    def test_r6_reason_not_in_whitelist(self, normal_order):
        """R6: 退货原因不在白名单 → 转人工"""
        decision = classify_refund(normal_order, "我就是想退钱", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R6"

    def test_r0_switch_off(self, normal_order, monkeypatch):
        """R0: 总开关关闭 → 全部转人工"""
        import agent.tools.ecom.refund_policy as rp
        monkeypatch.setattr(rp, "AUTO_APPROVE_ENABLED", False)
        decision = classify_refund(normal_order, "不喜欢", "C001")
        assert decision.action == "manual"
        assert decision.rule == "R0"
```

### 步骤 3：运行测试

```bash
pip install pytest pytest-asyncio
pytest tests/ -v --tb=short
```

在 CI 中强制要求覆盖率：

```bash
pip install pytest-cov
pytest tests/ --cov=agent --cov-report=term-missing --cov-fail-under=80
```

---

## 3.2 转人工闭环

### 问题

`EscalateToHumanTool` 只创建工单记录，没有真正通知人工客服。

### 解决方案

#### 步骤 1：定义人工客服通知接口

新建 `channels/human_agent.py`：

```python
"""人工客服通知适配器"""

from abc import ABC, abstractmethod


class HumanAgentNotifier(ABC):
    @abstractmethod
    async def notify(self, ticket_id: str, customer_id: str, summary: str, channel: str) -> bool:
        """通知人工客服有新工单"""
        ...


class FeishuNotifier(HumanAgentNotifier):
    """通过飞书群机器人通知客服团队"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def notify(self, ticket_id, customer_id, summary, channel):
        import httpx
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": "🔔 新工单待处理"}},
                "elements": [
                    {"tag": "div", "text": {"content": f"**工单号**：{ticket_id}", "tag": "lark_md"}},
                    {"tag": "div", "text": {"content": f"**用户**：{customer_id}", "tag": "lark_md"}},
                    {"tag": "div", "text": {"content": f"**来源**：{channel}", "tag": "lark_md"}},
                    {"tag": "div", "text": {"content": f"**摘要**：{summary}", "tag": "lark_md"}},
                ],
            },
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.webhook_url, json=payload)
            return resp.status_code == 200
```

#### 步骤 2：改造 EscalateToHumanTool

```python
class EscalateToHumanTool(Tool):
    def __init__(self, actor=None, logged_in=False, notifier=None):
        self._actor = actor
        self._logged_in = logged_in
        self._notifier = notifier  # 注入通知器

    async def execute(self, **kwargs):
        # ... 创建工单逻辑不变 ...
        ticket = await repository.create_ticket(...)

        # 通知人工客服
        if self._notifier:
            await self._notifier.notify(
                ticket_id=ticket["ticket_id"],
                customer_id=self._actor,
                summary=kwargs.get("summary", ""),
                channel="web",
            )

        return f"已为您转接人工客服，工单号：{ticket['ticket_id']}，客服人员会尽快与您联系。"
```

---

## 3.3 RAG 知识库

### 问题

商品政策、FAQ、售后条款全靠 identity.md 硬编码，无法扩展。

### 解决方案

#### 步骤 1：安装依赖

```bash
pip install chromadb==0.5.20 sentence-transformers==3.3.0 rank-bm25==0.2.2
```

#### 步骤 2：构建知识库

新建 `rag/knowledge_base.py`：

```python
"""RAG 知识库：商品政策、FAQ、售后条款的语义检索"""

import chromadb
from sentence_transformers import SentenceTransformer


class KnowledgeBase:
    def __init__(self, persist_dir: str = "data/chroma"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name="ecom_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
        self.model = SentenceTransformer("all-MiniLM-L6-v2")

    def add_documents(self, documents: list[dict]):
        """批量添加文档
        documents: [{"id": "faq_001", "text": "...", "category": "FAQ"}, ...]
        """
        ids = [d["id"] for d in documents]
        texts = [d["text"] for d in documents]
        metadatas = [{"category": d.get("category", "")} for d in documents]
        embeddings = self.model.encode(texts).tolist()

        self.collection.upsert(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def query(self, question: str, top_k: int = 3) -> list[dict]:
        """语义检索相关知识"""
        query_embedding = self.model.encode([question]).tolist()
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
        )
        return [
            {"text": doc, "category": meta.get("category", ""), "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]
```

#### 步骤 3：在 Agent 上下文中注入检索结果

修改 `agent/context.py` 的 `build_messages`：

```python
class ContextBuilder:
    def __init__(self, ..., knowledge_base=None):
        self.knowledge_base = knowledge_base

    def build_messages(self, history, current_message):
        system_prompt = self._build_system_prompt()

        # RAG 检索：如果有知识库，检索相关知识注入 system prompt
        if self.knowledge_base:
            relevant_docs = self.knowledge_base.query(current_message, top_k=3)
            if relevant_docs:
                knowledge_section = "\n\n## 相关知识库（仅供参考，以工具返回为准）\n"
                for i, doc in enumerate(relevant_docs, 1):
                    knowledge_section += f"{i}. [{doc['category']}] {doc['text']}\n"
                system_prompt += knowledge_section

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": current_message})
        return messages
```

---

## 3.4 Docker 化部署

### 步骤 1：编写 Dockerfile

新建 `Dockerfile`：

```dockerfile
# 多阶段构建
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 运行时阶段
FROM python:3.11-slim

WORKDIR /app

# 复制依赖
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制代码
COPY . .

# 非 root 用户运行
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080

CMD ["python", "main.py"]
```

### 步骤 2：编写 docker-compose.yml

```yaml
version: "3.8"

services:
  app:
    build: .
    ports:
      - "8080:8080"
    env_file:
      - .env
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: "2.0"

  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: ecom_db
      MYSQL_USER: ecom_user
      MYSQL_PASSWORD: ${DB_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
      - ./migrations:/docker-entrypoint-initdb.d
    ports:
      - "3306:3306"
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf
      - ./deploy/certs:/etc/nginx/certs:ro
    depends_on:
      - app
    restart: unless-stopped

volumes:
  mysql_data:
  redis_data:
```

### 步骤 3：Nginx 配置

新建 `deploy/nginx.conf`：

```nginx
upstream nanoclaw_app {
    server app:8080;
    keepalive 32;
}

server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    # 限流
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

    location / {
        proxy_pass http://nanoclaw_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket 支持
    location /ws {
        proxy_pass http://nanoclaw_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;  # WebSocket 长连接超时
    }

    # API 限流
    location /api/ {
        limit_req zone=api_limit burst=20 nodelay;
        proxy_pass http://nanoclaw_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 步骤 4：添加健康检查端点

在 `channels/web.py` 中添加：

```python
@self._app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
```

### 步骤 5：启动

```bash
# 构建并启动
docker-compose up -d --build

# 查看日志
docker-compose logs -f app

# 扩容（多实例）
docker-compose up -d --scale app=3
```

---

## 3.5 前端工程化

### 问题

三个 HTML 单文件（共 100KB+），内嵌 JS/CSS，无构建流程。

### 解决方案（中长期）

```bash
# 初始化 Vue3 项目
npm create vite@latest web-ui -- --template vue-ts
cd web-ui

# 安装依赖
npm install vue-router@4 pinia axios element-plus

# 目录结构
web-ui/
├── src/
│   ├── views/
│   │   ├── UserChat.vue       # 用户端
│   │   ├── AgentDesk.vue      # 客服工作台
│   │   └── AdminDashboard.vue # 管理后台
│   ├── components/
│   │   ├── ChatWindow.vue
│   │   ├── MessageBubble.vue
│   │   └── SessionList.vue
│   ├── stores/
│   │   ├── auth.ts            # 登录状态
│   │   └── chat.ts            # 聊天状态
│   ├── api/
│   │   └── index.ts           # API 封装
│   └── router/
│       └── index.ts
├── vite.config.ts
└── package.json
```

构建后将 `dist/` 目录挂载到 Nginx，由 Nginx 直接服务静态文件，Python 后端只负责 API 和 WebSocket。

---

# 四、落地路线图（周计划）

| 周次 | 任务 | 产出 | 验收标准 |
|------|------|------|---------|
| **第1周** | P0-安全：密钥外移、密码哈希、JWT鉴权、ExecTool下线 | `.env` + `security/` 模块 + 改造后的 web.py | 未登录访问管理API返回401；代码库无明文密钥 |
| **第2周** | P0-数据层：MySQL表结构、ORM模型、Repository层、迁移脚本 | `db/` 模块 + `migrations/` + 迁移完成 | 并发写不丢数据；崩溃后数据完整 |
| **第3周** | P0-并发：Gateway并发消费、信号量限流、超时控制 | 改造后的 `gateway.py` | 20并发总耗时≈单条耗时；超时返回友好提示 |
| **第4周** | P1-稳定性：Agent LRU、LLM重试/限流、日志体系、依赖精简 | `utils/logger.py` + `providers/rate_limiter.py` + 新requirements.txt | 7×24小时运行内存不增长；API失败自动重试 |
| **第5周** | P2-测试：退款规则单测、工具单测、CI集成 | `tests/` 目录 + 覆盖率≥80% | `pytest` 全绿；CI 强制覆盖率门禁 |
| **第6周** | P2-业务闭环：转人工通知、RAG知识库、Docker部署 | `Dockerfile` + `docker-compose.yml` + RAG模块 | `docker-compose up` 一键启动；转人工客服收到通知 |
| **第7周** | P2-前端工程化 + 压测调优 | Vue3 前端 + 压测报告 | 支持 100 并发用户，P99 响应 < 5s |
| **第8周** | 灰度上线 + 监控告警 | 监控面板 + 告警规则 | 错误率 < 1%，可用率 > 99.9% |

---

# 五、上线前验证清单

## 安全验证
- [ ] 代码仓库中无任何明文密钥（`git log -p | grep -i "api_key\|secret\|password"`）
- [ ] 用户密码均为 bcrypt 哈希
- [ ] 未登录访问 `/api/stats` 返回 401
- [ ] 普通用户访问管理端 API 返回 403
- [ ] ExecTool 已移除或白名单限制
- [ ] HTTPS 已配置，HTTP 自动跳转
- [ ] CORS 已配置具体域名（非 `*`）

## 数据验证
- [ ] 所有业务数据存储在 MySQL，无 JSON 文件读写
- [ ] 并发写操作不丢失数据（10 并发测试通过）
- [ ] 数据库连接池配置合理（pool_size + max_overflow）
- [ ] 会话历史存储在 Redis，设置了 TTL

## 稳定性验证
- [ ] Gateway 支持并发消费（20 并发总耗时 ≈ 单条耗时）
- [ ] LLM 调用有超时（30s）和重试（3次指数退避）
- [ ] 全局限流器生效（不超过 API 配额）
- [ ] Agent 实例有 LRU 淘汰（MAX_AGENTS 上限）
- [ ] 单条消息处理超时（120s）返回友好提示
- [ ] 7×24 小时压测内存稳定（无泄漏）

## 可观测性验证
- [ ] 所有 print 替换为 logger
- [ ] 日志输出 JSON 格式到文件
- [ ] 关键错误有告警（飞书/邮件）
- [ ] `/health` 端点可访问
- [ ] Docker 健康检查通过

## 业务验证
- [ ] 退款规则引擎单测覆盖率 100%
- [ ] 转人工后客服收到通知
- [ ] 写操作（改地址/退款）有事务保护
- [ ] 订单归属校验生效（不能查别人订单）
- [ ] 隐私脱敏生效（手机号/地址/姓名）

---

> **文档版本**：v1.0
> **基于代码版本**：V3（2026-09-07 审查）
> **维护建议**：每完成一个 P0/P1 项，在此文档对应位置打勾并记录完成日期。
