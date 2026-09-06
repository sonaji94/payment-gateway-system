"""Application configuration using pydantic-settings.

Centralises all environment-driven settings so the rest of the codebase can
import a single immutable ``settings`` object.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core ------------------------------------------------------------------
    app_name: str = "payment-gateway"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/v1"
    secret_key: str = "change-me"

    # Database ----------------------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://payment:payment@localhost:5432/payment_gateway"
    )
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_recycle: int = 1800
    db_pool_timeout: int = 30

    # Redis --------------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_idempotency_db: int = 1
    redis_ratelimit_db: int = 2

    # JWT / security ------------------------------------------------------------
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    api_key_salt: str = "change-me"

    # Celery --------------------------------------------------------------------
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_webhook_max_retries: int = 5
    celery_webhook_retry_backoff: float = 30.0

    # PSP / webhooks --------------------------------------------------------------
    upi_bank_callback_url: str = "https://mock.psp.example/v1/webhooks/bank-callback"
    webhook_default_base_url: str = "https://merchant.example/webhook"
    webhook_verify_tolerance_seconds: int = Field(default=300, ge=0)
    psp_webhook_secret: str = "change-me-psp-webhook-secret"
    merchant_webhook_secret: str = "change-me-merchant-webhook-secret"

    # Rate limiting -----------------------------------------------------------------
    rate_limit_default_per_minute: int = 120
    rate_limit_orders_per_minute: int = 60

    @field_validator("secret_key", "api_key_salt")
    @classmethod
    def _reject_default_secret(cls, v: str) -> str:
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached, immutable settings singleton."""
    return Settings()


settings = get_settings()
