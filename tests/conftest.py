"""Shared pytest fixtures: async DB (sqlite) + fakeredis + API client."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import (  # noqa: F401 (register all tables)
    LedgerAccount,
    LedgerEntry,
    Merchant,
    PaymentIntent,
    Transaction,
    UpiVpa,
    WebhookDelivery,
)

TEST_DB_URL = "sqlite+aiosqlite://"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        TEST_DB_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture
async def db_session(
    db_session_factory,
) -> AsyncGenerator[AsyncSession, None]:
    async with db_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def api_client(db_session_factory):
    """FastAPI client with overridden DB dependency and fakeredis middleware."""

    async def _override():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override

    fake_idem = fakeredis.aioredis.FakeRedis(decode_responses=True)
    fake_ratelimit = fakeredis.aioredis.FakeRedis(decode_responses=True)
    IdempotencyMiddleware._client = fake_idem
    RateLimitMiddleware._client = fake_ratelimit

    # Stub Celery dispatch so tests never touch a real broker.
    import app.api.v1.webhooks as webhooks_mod

    class _StubTask:
        def delay(self, *args, **kwargs):
            return None

    webhooks_mod.deliver_merchant_webhook = _StubTask()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"