"""ORMs for payment intents, transactions and registered UPIs with an explicit
state machine and strict ``Numeric(18, 4)`` money columns."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.merchant import Merchant


class PaymentStatus(enum.StrEnum):
    """Lifecycle of a payment intent."""

    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class TransactionState(enum.StrEnum):
    """Fine-grained state of a single payment attempt."""

    INITIATED = "INITIATED"
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PaymentMethod(enum.StrEnum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"


class PaymentIntent(UUIDMixin, Base):
    """A merchant-facing order / payment intent awaiting fulfilment."""

    __tablename__ = "payment_intents"

    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"),
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(
        String(20), default=PaymentStatus.CREATED.value, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    merchant: Mapped[Merchant] = relationship(back_populates="payment_intents")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="intent", cascade="all, delete-orphan"
    )


class Transaction(UUIDMixin, Base):
    """A single payment attempt under a payment intent."""

    __tablename__ = "transactions"

    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="CASCADE"),
        index=True,
    )
    vpa: Mapped[str] = mapped_column(String(255), index=True)
    payment_method: Mapped[str] = mapped_column(
        String(20), default=PaymentMethod.UPI.value
    )
    state: Mapped[str] = mapped_column(
        String(20), default=TransactionState.INITIATED.value, index=True
    )
    psp_reference: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    intent: Mapped[PaymentIntent] = relationship(back_populates="transactions")


class UpiVpa(UUIDMixin, Base):
    """A vpa that a merchant has registered/whitelisted for UPI Collects."""

    __tablename__ = "upi_vpas"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    vpa: Mapped[str] = mapped_column(String(255), index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )