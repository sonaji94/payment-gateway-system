"""UPI payment initiation service.

Flow:
    ``PaymentIntent.CREATED -> PaymentIntent.PROCESSING``
    ``Transaction.INITIATED -> Transaction.PENDING``

Both transitions happen in one transaction after locking the intent with
``FOR UPDATE`` to guarantee a single worker advances the state machine.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import new_id
from app.models import (
    Merchant,
    PaymentIntent,
    PaymentMethod,
    PaymentStatus,
    Transaction,
    TransactionState,
)
from app.schemas.payment import UpiInitiateRequest, UpiInitiateResponse
from app.services.state_machine import (
    assert_intent_transition,
    assert_transaction_transition,
)

logger = get_logger(__name__)


class PaymentServiceError(Exception):
    """Base exception for payment service failures."""


class OrderNotFound(PaymentServiceError):
    pass


class AlreadyFinalised(PaymentServiceError):
    """The intent is SUCCESS/FAILED and can no longer be initiated."""


class DuplicateTransaction(PaymentServiceError):
    """An identical transaction was already started for this vpa + key."""


async def initiate_upi(
    db: AsyncSession, merchant: Merchant, payload: UpiInitiateRequest
) -> UpiInitiateResponse:
    """Create a UPI transaction and move the intent into PROCESSING.

    Pessimistic locking: the intent row is selected with ``FOR UPDATE`` so two
    concurrent initiation requests cannot both advance the state machine.
    """
    stmt = (
        select(PaymentIntent)
        .where(
            PaymentIntent.public_id == payload.order_id,
            PaymentIntent.merchant_id == merchant.id,
        )
        .with_for_update()
    )
    intent = (await db.execute(stmt)).scalar_one_or_none()
    if intent is None:
        raise OrderNotFound(f"Order {payload.order_id} not found")

    # Reject initiation when the intent is already finalised.
    if intent.status in {PaymentStatus.SUCCESS.value, PaymentStatus.FAILED.value}:
        raise AlreadyFinalised(
            f"Order {payload.order_id} already {intent.status}"
        )

    # Idempotency: an existing PENDING transaction for the same vpa is returned.
    duplicate = (
        await db.execute(
            select(Transaction).where(
                Transaction.intent_id == intent.id,
                Transaction.vpa == payload.vpa,
                Transaction.payment_method == PaymentMethod.UPI.value,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        logger.info(
            "payment_initiate_replay",
            transaction_id=duplicate.public_id,
            order_id=intent.public_id,
        )
        return UpiInitiateResponse(
            transaction_id=duplicate.public_id,
            order_id=intent.public_id,
            status=TransactionState.PENDING.value,
            psp_reference=duplicate.psp_reference,
        )

    # --- State machine transitions (validated) --------------------------
    assert_intent_transition(
        PaymentStatus(intent.status), PaymentStatus.PROCESSING
    )
    intent.status = PaymentStatus.PROCESSING.value

    transaction = Transaction(
        public_id=new_id("txn"),
        intent_id=intent.id,
        vpa=payload.vpa,
        payment_method=PaymentMethod.UPI.value,
        state=TransactionState.PENDING.value,
        psp_reference=new_id("psp", 12),
    )
    assert_transaction_transition(
        TransactionState.INITIATED, TransactionState.PENDING
    )
    db.add(transaction)

    await db.commit()
    await db.refresh(intent)
    await db.refresh(transaction)

    logger.info(
        "payment_initiated",
        transaction_id=transaction.public_id,
        order_id=intent.public_id,
        vpa=transaction.vpa,
        amount=intent.amount,
        psp_reference=transaction.psp_reference,
    )
    return UpiInitiateResponse(
        transaction_id=transaction.public_id,
        order_id=intent.public_id,
        status=transaction.state,
        psp_reference=transaction.psp_reference,
    )