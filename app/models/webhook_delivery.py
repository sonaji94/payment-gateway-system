"""ORM for outbound merchant webhook deliveries (outbox pattern)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class DeliveryState(enum.StrEnum):
    """Lifecycle of a merchant webhook delivery attempt."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class WebhookDelivery(UUIDMixin, Base):
    """A durable record of an outbound notification to a merchant.

    Functions as an outbox: merchant URL + payload are stored before Celery
    dispatch so failed deliveries can be replayed and audited.
    """

    __tablename__ = "webhook_deliveries"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    payload: Mapped[str] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(
        String(20), default=DeliveryState.PENDING.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )