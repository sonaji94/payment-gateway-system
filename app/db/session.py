"""Async SQLAlchemy 2.0 engine + session factory (asyncpg).

Connection pooling is tuned for a high-throughput API: a bounded pool with
``pool_pre_ping`` to evict stale connections quickly and ``expire_on_commit=False``
so committed objects remain usable without extra queries.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def build_engine() -> AsyncEngine:
    """Create the configured async engine bound to ``asyncpg`` via URL."""
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
    )


engine: AsyncEngine = build_engine()

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a session, rolling back on exceptions."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise