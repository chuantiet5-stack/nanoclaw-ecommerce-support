"""SQLAlchemy ORM 模型（生产落地 P0-1.1 步骤 4）。

与 migrations/001_init.sql 表结构一一对应。
"""

from sqlalchemy import (JSON, BigInteger, Column, DateTime, Float, Integer,
                        String, Text, func)

from db.database import Base

# 主键类型：MySQL 渲染 BIGINT；sqlite（集成测试）渲染 INTEGER 以支持自增
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    customer_id = Column(String(64), unique=True, nullable=False)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(64), nullable=False)
    phone = Column(String(20), default="")
    # 与 data/users.json 档案字段对齐（生产落地 P0-1.1：委托层需要）
    level = Column(String(32), default="普通会员")
    points = Column(Integer, default=0)
    role = Column(String(16), default="user")   # user / admin
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Goods(Base):
    __tablename__ = "goods"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    # 字段与 data/goods.json 完全对齐（工具用 sku/highlights/sizes/scenes/link）
    sku = Column(String(64), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    category = Column(String(64), default="")
    price = Column(Float, nullable=False)
    highlights = Column(String(512), default="")
    description = Column(Text, default="")
    stock = Column(Integer, default=0)
    sizes = Column(JSON, default=None)
    scenes = Column(JSON, default=None)
    link = Column(String(512), default="")


class Order(Base):
    __tablename__ = "orders"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
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


class Logistics(Base):
    __tablename__ = "logistics"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    order_id = Column(String(64), nullable=False, index=True)
    carrier = Column(String(64), default="")
    tracking_no = Column(String(64), default="")
    status = Column(String(32), default="待发货")
    traces = Column(JSON, default=None)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    ticket_id = Column(String(64), unique=True, nullable=False)
    customer_id = Column(String(64), nullable=False, index=True)
    order_id = Column(String(64), default="")
    type = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="待处理")
    priority = Column(Integer, default=1)
    summary = Column(String(512), default="")
    assigned_to = Column(String(64), default="")
    # 与 data/tickets.json 字段对齐（生产落地 P0-1.1：委托层需要）
    description = Column(Text, default="")
    priority_label = Column(String(16), default="普通")   # 普通 / 加急
    records = Column(JSON, default=None)                  # 处理记录列表
    updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class RefundRecord(Base):
    __tablename__ = "refund_records"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    refund_id = Column(String(64), unique=True, nullable=False)
    order_id = Column(String(64), nullable=False, index=True)
    customer_id = Column(String(64), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    reason = Column(String(255), default="")
    decided_by = Column(String(16), nullable=False)
    decision = Column(String(16), nullable=False)
    rule_hit = Column(String(16), default="")
    created_at = Column(DateTime, server_default=func.now())


class RefundAuditLog(Base):
    __tablename__ = "refund_audit_log"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    refund_id = Column(String(64), nullable=False, index=True)
    customer_id = Column(String(64), nullable=False)
    order_id = Column(String(64), nullable=False)
    action = Column(String(16), nullable=False)
    reason = Column(String(512), default="")
    rule_hit = Column(String(16), default="")
    profile_snapshot = Column(JSON, default=None)
    # 与 refund_policy.append_audit 的字段对齐（生产落地 P0-1.1）
    detail = Column(Text, default="")
    amount = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class UserPreference(Base):
    __tablename__ = "user_preferences"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    customer_id = Column(String(64), nullable=False, index=True)
    content = Column(String(512), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
