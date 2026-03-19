"""
OpenClaw TeamLab — Database Connections (MySQL + Redis)
Async connection pools with lifecycle management.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings

logger = logging.getLogger("teamlab.db")

# ── SQLAlchemy Async Engine ──
engine = create_async_engine(
    settings.MYSQL_DSN,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=settings.DEBUG,
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_db():
    """Yield an async DB session, auto-commit on success, rollback on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Redis Async Pool ──
_redis_pool: aioredis.Redis | None = None
_redis_init_lock: asyncio.Lock | None = None


def _get_redis_lock() -> asyncio.Lock:
    """Lazily create the asyncio Lock in the running event loop."""
    global _redis_init_lock
    if _redis_init_lock is None:
        _redis_init_lock = asyncio.Lock()
    return _redis_init_lock


async def get_redis() -> aioredis.Redis:
    """
    Get or create the global Redis connection pool.
    Uses an asyncio.Lock to prevent multiple coroutines from
    simultaneously creating duplicate pools.
    """
    global _redis_pool
    if _redis_pool is not None:
        return _redis_pool
    async with _get_redis_lock():
        # Double-checked locking: re-check after acquiring lock
        if _redis_pool is None:
            _redis_pool = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                max_connections=50,
            )
    return _redis_pool


def rkey(key: str) -> str:
    """Prefix a Redis key with the project namespace."""
    return f"{settings.REDIS_PREFIX}:{key}"


# ── Lifecycle ──
async def init_db():
    """Test database connectivity on startup."""
    # MySQL
    async with engine.begin() as conn:
        await conn.execute(
            __import__("sqlalchemy").text("SELECT 1")
        )
    logger.info("✓ MySQL connected: %s:%s/%s", settings.MYSQL_HOST, settings.MYSQL_PORT, settings.MYSQL_DATABASE)

    # Redis
    r = await get_redis()
    await r.ping()
    logger.info("✓ Redis connected: %s:%s/%s", settings.REDIS_HOST_DISPLAY, settings.REDIS_PORT, settings.REDIS_DB)


async def close_db():
    """Close all database connections."""
    global _redis_pool
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None
    await engine.dispose()
    logger.info("Database connections closed")
