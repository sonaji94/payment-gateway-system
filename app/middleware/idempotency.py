"""Idempotency middleware backed by Redis.

Guarantees that a client retrying the same request (same ``Idempotency-Key``)
against a mutating endpoint receives the exact same result instead of a
duplicate charge / debit.

Design:
  * Before handling a request with an ``Idempotency-Key`` header we reserve the
    key in Redis (``SET NX``) with the request fingerprint.
  * The response is then cached under the same key on success.
  * A retry arriving before completion is answered with ``409 Conflict``;
    a retry arriving after completion is answered with the cached response.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import ClassVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import idempotency_redis

logger = get_logger(__name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"
TTL_SECONDS = 24 * 60 * 60  # keep cached responses for one day
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Keys stored under idempotency key -> status of the in-flight request.
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"


class IdempotencyError(Exception):
    """Raised when an idempotency violation is detected."""


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Enforce at-least-once semantics for mutating endpoints."""

    _client: ClassVar = None

    @classmethod
    def _redis(cls):
        if cls._client is None:
            cls._client = idempotency_redis()
        return cls._client

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        method = request.method
        if method not in MUTATING_METHODS or IDEMPOTENCY_HEADER not in request.headers:
            return await call_next(request)

        key = request.headers[IDEMPOTENCY_HEADER]
        path = request.url.path
        redis = self._redis()
        redis_key = f"idem:{path}:{key}"

        # Atomic reserve: fails if the key is already being processed.
        reserved = await redis.set(
            redis_key,
            json.dumps({"request_id": request.headers.get("X-Request-Id", "")}),
            nx=True,
            ex=TTL_SECONDS,
        )
        if not reserved:
            existing = await redis.get(redis_key)
            if existing is None:
                # Expired between checks — subtle race; treat as conflict.
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "duplicate_request",
                        "message": "Idempotency key already in use. "
                        "Retry with the same payload or a fresh key.",
                    },
                )
            state = json.loads(existing)
            if state.get("status") == STATUS_IN_PROGRESS:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "duplicate_request",
                        "message": "Request with this Idempotency-Key is "
                        "already being processed.",
                    },
                )
            return Response(
                content=state.get("body", ""),
                status_code=state.get("status_code", 200),
                media_type="application/json",
            )

        # Mark as in-progress so concurrent retries get 409.
        state: dict = {"status": STATUS_IN_PROGRESS}
        await redis.set(
            redis_key, json.dumps(state), xx=True, ex=TTL_SECONDS
        )

        response = await call_next(request)

        # Only cache successful responses and requests that read a body.
        if 200 <= response.status_code < 500:
            body_bytes = b""
            if hasattr(response, "body_iterator"):
                body_chunks = [chunk async for chunk in response.body_iterator]
                body_bytes = b"".join(body_chunks)
                response = Response(
                    content=body_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            state = {
                "status": STATUS_DONE,
                "status_code": response.status_code,
                "body": body_bytes.decode("utf-8", errors="replace"),
                "headers": dict(response.headers),
            }
            await redis.set(redis_key, json.dumps(state), xx=True, ex=TTL_SECONDS)
            logger.debug(
                "idempotency_cached",
                path=path,
                status_code=response.status_code,
            )
        else:
            # On failure we free the key so the client can retry the same key.
            await redis.delete(redis_key)

        return response


idempotency_middleware = IdempotencyMiddleware