"""Double-entry ledger ORM: accounts and immutable entries.

Money is stored as ``Numeric(18, 4)`` mapped to Python ``Decimal`` — never
floats — so rounding is exact for INR reconciliation.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class AccountType(enum.StrEnum):
    """Kinds of ledger account the gateway maintains."""

    VIRTUAL = "VIRTUAL"                # money collected from a payer (per vpa)
    MERCHANT_SETTLEMENT = "MERCHANT_SETTLEMENT"  # payouts owed to a merchant
    PLATFORM_FEE = "PLATFORM_FEE"      # gateway revenue accrual


class EntryType(enum.StrEnum):
    """Side of a ledger line per double-entry bookkeeping."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerAccount(UUIDMixin, Base):
    """A single balance-bearing account in the GL."""

    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("identifier", "currency", name="uq_acct_currency"),)

    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    account_type: Mapped[AccountType] = mapped_column(
        Enum(
            AccountType,
            name="account_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        index=True,
    )
    identifier: Mapped[str] = mapped_column(String(128), index=True)  # e.g. vpa | merchant id
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LedgerEntry(UUIDMixin, Base):
    """An immutable line in the accounting ledger.

    Entries are append-only — never updated or deleted — preserving an auditable
    history. Every movement posts both a DEBIT and a CREDIT to keep the ledger
    balanced to zero.
    """

    __tablename__ = "ledger_entries"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    entry_type: Mapped[EntryType] = mapped_column(
        Enum(
            EntryType,
            name="entry_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<LedgerEntry id={self.id} {self.entry_type.value} "
            f"{self.amount} {self.currency} acct={self.account_id}>"
        )