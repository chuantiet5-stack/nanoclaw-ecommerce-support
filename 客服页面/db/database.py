"""数据库连接管理（SQLAlchemy 异步 + Redis）（生产落地 P0-1.1 步骤 3）。

- 连接参数从环境变量读取（DB_URL / REDIS_URL，见 .env.example），禁止硬编码
- 引擎与客户端均为惰性连接：import 本模块不要求 MySQL/Redis 正在运行
"""

import os

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

# 从环境变量读取，禁止硬编码
DB_URL = os.getenv(
    "DB_URL",
    "mysql+aiomysql://ecom_user:strong_password@localhost:3306/ecom_db",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# 异步引擎（连接池；create_async_engine 不会立即建连）
engine = create_async_engine(
    DB_URL,
    pool_size=10,        # 连接池大小
    max_overflow=20,     # 超出池大小的最大临时连接
    pool_timeout=30,     # 获取连接超时
    pool_recycle=1800,   # 连接回收时间（秒），防止 MySQL 8 小时断连
    echo=False,          # 生产环境关闭 SQL 日志
)

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

# Redis 客户端（from_url 惰性建连）
redis_client = aioredis.from_url(
    REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50,
)


async def get_db() -> AsyncSession:
    """依赖注入用：获取数据库会话。"""
    async with AsyncSessionLocal() as session:
        yield session
