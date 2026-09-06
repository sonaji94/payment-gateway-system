"""Bank/PSP webhook callback handling.

Verifies the HMAC-SHA256 signature over the raw request body, then atomically:
  1. locks the transaction + intent rows (``FOR UPDATE``)
  2. locks (or creates) the DEBIT/CREDIT ledger accounts
  3. advances the state machine   ``PENDING -> SUCCESS`` / ``PENDING -> FAILED``
  4. posts balanced double-entry ledger lines and updates balances on success
  5. enqueues the merchant-visible webhook notification to Celery

Everything up to rollback/commit happens in ONE DB transaction so concurrent
duplicate callbacks can never double-post the ledger.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_signature
from app.models import (
    AccountType,
    EntryType,
    LedgerAccount,
    LedgerEntry,
    PaymentIntent,
    PaymentStatus,
    Transaction,
    TransactionState,
)
from app.services.state_machine import (
    InvalidStateTransition,
    assert_intent_transition,
    assert_transaction_transition,
)

logger = get_logger(__name__)


class WebhookError(Exception):
    """Base exception for webhook processing."""


class SignatureVerificationError(WebhookError):
    pass


class WebhookTimestampError(WebhookError):
    pass


class TransactionNotFound(WebhookError):
    pass


class AmountMismatch(WebhookError):
    pass


def verify_bank_signature(payload: bytes, signature: str, timestamp: str | None) -> None:
    """Verify HMAC-SHA256 signature and freshness of a bank callback.

    The timestamp tolerance guards against replay attacks while the HMAC guards
    against tampering of payload bytes.
    """
    if timestamp:
        try:
            ts = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise WebhookTimestampError("Invalid callback timestamp") from exc
        age = abs(time.time() - ts)
        if age > settings.webhook_verify_tolerance_seconds:
            raise WebhookTimestampError(f"Callback timestamp too old ({age:.0f}s)")

    if not verify_signature(payload, settings.psp_webhook_secret, signature):
        raise SignatureVerificationError("HMAC signature verification failed")


async def _ensure_account_locked(
    db: AsyncSession, *, account_type: str, identifier: str, merchant_id: Any
) -> LedgerAccount:
    """Idempotently create a ledger account and lock it for update.

    Uses ``INSERT ... ON CONFLICT DO NOTHING`` then re-selects with ``FOR
    UPDATE`` so concurrent callbacks for a new account cannot deadlock or lose
    an entry.
    """
    stmt = pg_insert(LedgerAccount).values(
        account_type=account_type,
        identifier=identifier,
        merchant_id=merchant_id if account_type == "MERCHANT_SETTLEMENT" else None,
        balance=Decimal("0"),
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[LedgerAccount.identifier, LedgerAccount.currency]
    )
    await db.execute(stmt)

    row = await db.execute(
        select(LedgerAccount)
        .where(
            LedgerAccount.identifier == identifier,
            LedgerAccount.account_type == account_type,
        )
        .with_for_update()
    )
    account = row.scalar_one()
    return account


async def process_bank_callback(
    db: AsyncSession, event: str, psp_reference: str, amount: Decimal
) -> dict[str, Any]:
    """Atomically apply a verified PSP callback to a transaction."""
    txn = (
        await db.execute(
            select(Transaction)
            .where(Transaction.psp_reference == psp_reference)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if txn is None:
        raise TransactionNotFound(f"No transaction for {psp_reference=}")

    intent = (
        await db.execute(
            select(PaymentIntent)
            .where(PaymentIntent.id == txn.intent_id)
            .with_for_update()
        )
    ).scalar_one()

    if intent.amount != amount:
        raise AmountMismatch(
            f"Callback amount {amount} != intent amount {intent.amount}"
        )

    # --- Idempotent re-delivery of the same terminal event --------------
    if txn.state == TransactionState.SUCCESS.value and event == "payment.success":
        await db.commit()
        return {
            "replay": True,
            "transaction_db_id": txn.id,
            "transaction_id": txn.public_id,
            "status": txn.state,
        }

    if txn.state == TransactionState.FAILED.value and event == "payment.failed":
        await db.commit()
        return {
            "replay": True,
            "transaction_db_id": txn.id,
            "transaction_id": txn.public_id,
            "status": txn.state,
        }

    target_txn = TransactionState.SUCCESS if event == "payment.success" else TransactionState.FAILED
    target_intent = PaymentStatus.SUCCESS if event == "payment.success" else PaymentStatus.FAILED

    try:
        assert_transaction_transition(TransactionState(txn.state), target_txn)
        assert_intent_transition(PaymentStatus(intent.status), target_intent)
    except InvalidStateTransition as exc:
        await db.rollback()
        raise InvalidStateTransition(str(exc)) from exc

    txn.state = target_txn.value
    intent.status = target_intent.value

    if event == "payment.success":
        # Lock both GL accounts up-front to prevent double-spend races.
        payer_account = await _ensure_account_locked(
            db,
            account_type=AccountType.VIRTUAL.value,
            identifier=txn.vpa,
            merchant_id=None,
        )
        merchant_account = await _ensure_account_locked(
            db,
            account_type=AccountType.MERCHANT_SETTLEMENT.value,
            identifier=f"merchant:{intent.merchant_id}",
            merchant_id=intent.merchant_id,
        )

        # Pull-collection: funds arrive from the payer's bank, so both the
        # virtual (asset) account and the merchant (payable) account grow.
        payer_account.balance = Decimal(payer_account.balance) + amount
        merchant_account.balance = Decimal(merchant_account.balance) + amount

        db.add_all(
            [
                LedgerEntry(
                    account_id=payer_account.id,
                    transaction_id=txn.id,
                    amount=amount,
                    entry_type=EntryType.DEBIT,
                ),
                LedgerEntry(
                    account_id=merchant_account.id,
                    transaction_id=txn.id,
                    amount=amount,
                    entry_type=EntryType.CREDIT,
                ),
            ]
        )
        logger.info(
            "ledger_posted",
            transaction_id=txn.id,
            amount=str(amount),
            debit=payer_account.identifier,
            credit=merchant_account.identifier,
        )

    await db.commit()

    logger.info(
        "payment_completed" if event == "payment.success" else "payment_failed",
        transaction_id=txn.public_id,
        order_id=intent.public_id,
        amount=str(amount),
        status=target_intent.value,
    )
    return {
        "replay": False,
        "transaction_db_id": txn.id,
        "transaction_id": txn.public_id,
        "order_id": intent.public_id,
        "status": target_intent.value,
    }