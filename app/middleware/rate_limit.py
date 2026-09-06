"""Fixed-window rate limiting middleware using Redis.

Limits requests per API key (``X-Api-Key``) or the client IP when unauthenticated.
A ``X-RateLimit-*`` header set is added so clients can self-throttle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import ratelimit_redis

logger = get_logger(__name__)

LIMIT_HEADERS = {
    "limit": "X-RateLimit-Limit",
    "remaining": "X-RateLimit-Remaining",
    "reset": "X-RateLimit-Reset",
}


class RateLimitExceeded(Exception):
    """Raised when the caller has exhausted its quota."""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-key request limits with a sliding fixed-window counter."""

    _client: ClassVar = None

    @classmethod
    def _redis(cls):
        if cls._client is None:
            cls._client = ratelimit_redis()
        return cls._client

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)

        identity = request.headers.get(
            "X-Api-Key", request.client.host if request.client else "unknown"
        )
        limit = 120

        redis = self._redis()
        window = 60
        now_ms = await self._now_ms(redis)
        bucket = int(now_ms / (window * 1000))
        key = f"rl:{identity}:{bucket}"
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window * 2)
        count, _ = await pipe.execute()

        reset_epoch = bucket * window
        if count > limit:
            body = {
                "error": "rate_limit_exceeded",
                "message": "Too many requests. Retry later.",
            }
            resp = JSONResponse(status_code=429, content=body)
            resp.headers[LIMIT_HEADERS["limit"]] = str(limit)
            resp.headers[LIMIT_HEADERS["remaining"]] = "0"
            resp.headers[LIMIT_HEADERS["reset"]] = str(reset_epoch)
            return resp

        response = await call_next(request)
        response.headers[LIMIT_HEADERS["limit"]] = str(limit)
        response.headers[LIMIT_HEADERS["remaining"]] = str(max(0, limit - count))
        response.headers[LIMIT_HEADERS["reset"]] = str(reset_epoch)
        return response

    @staticmethod
    async def _now_ms(redis) -> int:
        data = await redis.time()
        seconds, micros = int(data[0]), int(data[1])
        return seconds * 1000 + micros // 1000


rate_limit_middleware = RateLimitMiddleware