"""Database connection and session management."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gateway.config import settings

# Convert database URL to async
_db_url = settings.database_url
if _db_url.startswith("sqlite:///"):
    _async_url = _db_url.replace("sqlite:///", "sqlite+aiosqlite:///")
elif _db_url.startswith("postgresql://"):
    _async_url = _db_url.replace("postgresql://", "postgresql+asyncpg://")
elif _db_url.startswith("postgres://"):
    _async_url = _db_url.replace("postgres://", "postgresql+asyncpg://")
else:
    _async_url = _db_url

engine = create_async_engine(
    _async_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """Get a database session."""
    async with async_session() as session:
        yield session


async def init_db():
    """Create all tables (dev/test only — use Alembic in production)."""
    from gateway.db.models import Base
    from gateway.db import rag_models  # noqa: F401 — register RAG tables
    from gateway.db import deploy_models  # noqa: F401 — register deploy tables
    from gateway.db import experiment_models  # noqa: F401 — register experiment tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Close the database engine."""
    await engine.dispose()
