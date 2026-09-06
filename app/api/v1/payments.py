"""UPI payment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession, MerchantDep
from app.core.logging import get_logger
from app.schemas.payment import UpiInitiateRequest, UpiInitiateResponse
from app.services.payment import (
    AlreadyFinalised,
    OrderNotFound,
    initiate_upi,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/upi/initiate",
    response_model=UpiInitiateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def initiate_upi_payment(
    payload: UpiInitiateRequest,
    merchant: MerchantDep,
    db: DbSession,
) -> UpiInitiateResponse:
    """Initiate a UPI Collect payment for an existing order.

    Drives the state machine ``CREATED -> PROCESSING`` with pessimistic row
    locking to prevent double-charges under concurrency.
    """
    try:
        return await initiate_upi(db, merchant, payload)
    except OrderNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except AlreadyFinalised as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "order_finalised", "message": str(exc)},
        ) from exc