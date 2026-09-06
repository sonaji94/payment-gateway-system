"""Double-entry ledger service.

Every successful payment posts two balancing entries — a DEBIT on the payer's
virtual account and a CREDIT on the merchant's settlement account — while
atomically updating their balances inside the same DB transaction as the state
change. ``SELECT ... FOR UPDATE`` on both accounts prevents double-spend races.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    AccountType,
    EntryType,
    LedgerAccount,
    LedgerEntry,
    Transaction,
)

logger = get_logger(__name__)


class LedgerError(Exception):
    """Base exception for ledger errors."""


class InsufficientBalance(LedgerError):
    """Raised when a debit would leave an account with a negative balance."""


async def _get_or_create_account(
    db: AsyncSession, *, account_type: AccountType, identifier: str, merchant_id: object
) -> LedgerAccount:
    """Create-if-missing then lock the GL account for update."""
    stmt = pg_insert(LedgerAccount).values(
        account_type=account_type.value,
        identifier=identifier,
        merchant_id=merchant_id
        if account_type is AccountType.MERCHANT_SETTLEMENT
        else None,
        balance=Decimal("0"),
    ).on_conflict_do_nothing(
        index_elements=[LedgerAccount.identifier, LedgerAccount.currency]
    )
    await db.execute(stmt)
    row = await db.execute(
        select(LedgerAccount)
        .where(
            LedgerAccount.identifier == identifier,
            LedgerAccount.account_type == account_type.value,
        )
        .with_for_update()
    )
    return row.scalar_one()


async def post_double_entry(
    db: AsyncSession,
    *,
    transaction: Transaction,
    payer_vpa: str,
    merchant_id: object,
    amount: Decimal,
    currency: str = "INR",
) -> tuple[LedgerEntry, LedgerEntry, LedgerAccount, LedgerAccount]:
    """Post a balanced DEBIT/CREDIT pair and update account balances.

    Runs inside the caller's transaction; a commit happens at the service
    layer boundary to keep the state change and ledger write atomic.
    """
    if amount <= 0:
        raise LedgerError("Cannot post a zero or negative ledger entry")

    debit_account = await _get_or_create_account(
        db,
        account_type=AccountType.VIRTUAL,
        identifier=payer_vpa,
        merchant_id=None,
    )
    credit_account = await _get_or_create_account(
        db,
        account_type=AccountType.MERCHANT_SETTLEMENT,
        identifier=f"merchant:{merchant_id}",
        merchant_id=merchant_id,
    )

    # Pull-collection semantics: funds arrive from the payer's bank, so the
    # virtual (asset) account *increases* in parallel with the merchant payable.
    debit_account.balance = Decimal(debit_account.balance) + amount
    credit_account.balance = Decimal(credit_account.balance) + amount

    debit_entry = LedgerEntry(
        account_id=debit_account.id,
        transaction_id=transaction.id,
        amount=amount,
        entry_type=EntryType.DEBIT,
        currency=currency,
    )
    credit_entry = LedgerEntry(
        account_id=credit_account.id,
        transaction_id=transaction.id,
        amount=amount,
        entry_type=EntryType.CREDIT,
        currency=currency,
    )
    db.add_all([debit_entry, credit_entry])

    logger.info(
        "ledger_posted",
        transaction_id=transaction.id,
        amount=str(amount),
        debit=debit_account.identifier,
        credit=credit_account.identifier,
    )
    return debit_entry, credit_entry, debit_account, credit_account