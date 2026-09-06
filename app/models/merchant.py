"""ORM model for payment merchants and their credentials."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.payment import PaymentIntent


class Merchant(UUIDMixin, Base):
    """A merchant account that initiates payments through the gateway.

    Credentials are never stored in plaintext — only salted hash digests of the
    API key and secret are persisted (see ``app.core.security.derive_key``).
    """

    __tablename__ = "merchants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    webhook_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    payment_intents: Mapped[list[PaymentIntent]] = relationship(
        back_populates="merchant", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Merchant id={self.id} name={self.name!r} active={self.is_active}>"