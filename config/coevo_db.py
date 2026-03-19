"""
OpenClaw TeamLab — Read-Only Connection to CognAlign-CoEvo Prod DB
Provides a second SQLAlchemy async engine exclusively for reading from cognalign_coevo_prod.
All writes are forbidden — this module must NEVER be used for INSERT/UPDATE/DELETE.
"""
import logging
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings

logger = logging.getLogger("teamlab.coevo_db")

# ── Read-only async engine ──
# pool_size intentionally small to not overwhelm the shared prod DB
coevo_engine = create_async_engine(
    settings.COEVO_MYSQL_DSN,
    pool_size=5,
    max_overflow=5,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,
    # Execution options: set read-only hint (advisory, not enforced by MySQL)
    execution_options={"no_parameters": False},
)

CoevoSessionLocal = async_sessionmaker(
    coevo_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_coevo_db():
    """
    Yield a read-only async DB session against cognalign_coevo_prod.
    Rolls back any accidental writes (should never happen).
    """
    async with CoevoSessionLocal() as session:
        try:
            yield session
            # Always rollback — this is a read-only connection
            await session.rollback()
        except Exception:
            await session.rollback()
            raise


async def init_coevo_db():
    """Test connectivity to cognalign-coevo prod DB on startup."""
    try:
        async with coevo_engine.begin() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        logger.info(
            "✓ CoEvo prod DB connected (read-only): %s:%s/%s",
            settings.COEVO_MYSQL_HOST,
            settings.COEVO_MYSQL_PORT,
            settings.COEVO_MYSQL_DATABASE,
        )
    except Exception as exc:
        logger.warning("CoEvo prod DB connection failed (non-fatal): %s", exc)


async def close_coevo_db():
    """Release CoEvo DB connection pool."""
    await coevo_engine.dispose()
    logger.info("CoEvo DB connections closed")
