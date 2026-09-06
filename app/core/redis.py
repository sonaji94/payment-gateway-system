"""Redis connection factories for idempotency, rate-limiting and Celery."""

from __future__ import annotations

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.core.config import settings


def _build_url(base: str, db: int) -> str:
    return f"{base.rstrip('/')}/{db}"


def idempotency_redis() -> Redis:
    """Return a dedicated Redis client for idempotency-key storage."""
    return aioredis.from_url(
        _build_url(settings.redis_url, settings.redis_idempotency_db),
        decode_responses=True,
    )


def ratelimit_redis() -> Redis:
    """Return a dedicated Redis client for rate-limit counters."""
    return aioredis.from_url(
        _build_url(settings.redis_url, settings.redis_ratelimit_db),
        decode_responses=True,
    )