"""Order intent endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, MerchantDep
from app.core.logging import get_logger
from app.models import PaymentIntent, PaymentStatus
from app.schemas.payment import CreateOrderRequest, CreateOrderResponse
from app.services.order import OrderIdempotencyReuse, create_order

logger = get_logger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    response_model=CreateOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order_endpoint(
    payload: CreateOrderRequest,
    merchant: MerchantDep,
    db: DbSession,
) -> CreateOrderResponse:
    """Create a payment order intent idempotently.

    Safe to retry with the same ``Idempotency-Key``; replays return the
    previously created order without creating a duplicate.
    """
    try:
        return await create_order(db, merchant, payload)
    except OrderIdempotencyReuse as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "idempotency_conflict", "message": str(exc)},
        ) from exc


@router.get("/{order_id}", response_model=CreateOrderResponse)
async def get_order(order_id: str, merchant: MerchantDep, db: DbSession) -> CreateOrderResponse:
    """Retrieve the status of an order intent."""
    result = await db.execute(
        select(PaymentIntent).where(
            PaymentIntent.public_id == order_id,
            PaymentIntent.merchant_id == merchant.id,
        )
    )
    intent = result.scalar_one_or_none()
    if intent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return CreateOrderResponse(
        order_id=intent.public_id,
        idempotency_key=intent.idempotency_key,
        amount=intent.amount,
        currency=intent.currency,
        status=PaymentStatus(intent.status).name,
    )