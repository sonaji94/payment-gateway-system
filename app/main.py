"""FastAPI application factory for the payment gateway."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware


def create_app() -> FastAPI:
    """Build and return the configured FastAPI ASGI application."""
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Middleware order matters: outermost = last added.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(IdempotencyMiddleware)
    application.add_middleware(RequestIdMiddleware)

    application.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Liveness probe (no DB dependency, safe for load balancers)."""
        return {"status": "ok"}

    return application


app = create_app()