"""Order creation service with idempotent intent handling."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import new_id
from app.models import Merchant, PaymentIntent, PaymentStatus
from app.schemas.payment import CreateOrderRequest, CreateOrderResponse

logger = get_logger(__name__)


class OrderServiceError(Exception):
    """Base exception for order service failures."""


class OrderIdempotencyReuse(OrderServiceError):
    """Raised when an idempotency key maps to a different payload."""


async def create_order(
    db: AsyncSession, merchant: Merchant, payload: CreateOrderRequest
) -> CreateOrderResponse:
    """Create a payment intent idempotently.

    The ``idempotency_key`` is scoped per merchant (unique at the DB level), so
    a retry of the exact same key returns the previously created intent.
    """
    stmt = select(PaymentIntent).where(
        PaymentIntent.idempotency_key == payload.idempotency_key,
        PaymentIntent.merchant_id == merchant.id,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing is not None:
        logger.info("order_idempotent_replay", order_id=existing.public_id)
        return CreateOrderResponse(
            order_id=existing.public_id,
            idempotency_key=existing.idempotency_key,
            amount=existing.amount,
            currency=existing.currency,
            status=PaymentStatus(existing.status).name.upper(),
        )

    intent = PaymentIntent(
        public_id=new_id("ord"),
        merchant_id=merchant.id,
        amount=payload.amount,
        currency=payload.currency,
        idempotency_key=payload.idempotency_key,
        description=payload.description,
        status=PaymentStatus.CREATED.value,
    )
    db.add(intent)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise OrderIdempotencyReuse(
            "Idempotency key already consumed with a different payload"
        ) from exc

    await db.refresh(intent)
    logger.info(
        "order_created",
        order_id=intent.public_id,
        merchant_id=intent.merchant_id,
        amount=intent.amount,
    )
    return CreateOrderResponse(
        order_id=intent.public_id,
        idempotency_key=intent.idempotency_key,
        amount=intent.amount,
        currency=intent.currency,
        status=intent.status,
    )