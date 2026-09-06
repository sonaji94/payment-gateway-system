"""Bank/PSP webhook callback endpoint.

NOT authenticated via merchant credentials — signatures are verified with the
PSP shared secret using HMAC-SHA256 over the raw request body, with a bounded
timestamp freshness window to prevent replays.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import sign_payload, verify_signature
from app.models import Merchant, WebhookDelivery
from app.schemas.webhook import WebhookAck
from app.services.webhook import (
    AmountMismatch,
    TransactionNotFound,
    process_bank_callback,
)
from app.workers.tasks import deliver_merchant_webhook

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/bank-callback")
async def bank_callback(
    request: Request,
    db: DbSession,
    x_signature: Annotated[str | None, Header()] = None,
    x_timestamp: Annotated[str | None, Header()] = None,
) -> WebhookAck:
    """Handle an asynchronous PSP callback for a UPI payment.

    1. Verifies the HMAC-SHA256 signature over the exact raw body.
    2. Applies the state transition and ledger post atomically.
    3. Writes a durable outbox row and enqueues merchant webhook delivery.

    Returns a lightweight 2xx ack so the PSP stops retrying.
    """
    if not x_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing X-Signature"
        )

    body = await request.body()

    # --- Step 1: constant-time signature + freshness verification ----------
    if not verify_signature(body, settings.psp_webhook_secret, x_signature):
        logger.warning("bank_callback_rejected", reason="bad_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="HMAC signature verification failed",
        )

    if x_timestamp is not None:
        try:
            ts = int(x_timestamp)
        except (TypeError, ValueError) as exc:
            logger.warning("bank_callback_rejected", reason="bad_timestamp")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid callback timestamp",
            ) from exc
        age = abs(time.time() - ts)
        if age > settings.webhook_verify_tolerance_seconds:
            logger.warning("bank_callback_rejected", reason="stale_timestamp", age_s=int(age))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Callback timestamp outside tolerance window",
            )

    try:
        event_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body"
        ) from exc

    event = event_data.get("event")
    psp_reference = event_data.get("psp_reference")
    amount = event_data.get("amount")
    if event not in {"payment.success", "payment.failed"} or not psp_reference:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed event payload"
        )

    # --- Step 2: atomic state transition + ledger posting -------------------
    try:
        result = await process_bank_callback(
            db, event=event, psp_reference=psp_reference, amount=Decimal(str(amount))
        )
    except (TransactionNotFound, AmountMismatch) as exc:
        logger.warning("bank_callback_rejected", reason=str(exc), event=event)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # --- Step 3: durable outbox + async delivery ----------------------------
    from app.models import PaymentIntent, Transaction

    merchant_row = await db.execute(
        select(Merchant)
        .join(PaymentIntent, PaymentIntent.merchant_id == Merchant.id)
        .join(Transaction, Transaction.intent_id == PaymentIntent.id)
        .where(
            Transaction.psp_reference == psp_reference,
        )
    )
    merchant = merchant_row.scalar_one_or_none()

    if merchant is not None and merchant.webhook_url and not result.get("replay", False):
        payload_bytes = json.dumps(
            {
                "event": event,
                "data": {
                    "transaction_id": result["transaction_id"],
                    "order_id": result.get("order_id"),
                    "status": result["status"],
                },
            }
        ).encode("utf-8")
        delivery = WebhookDelivery(
            transaction_id=result["transaction_db_id"],
            merchant_id=merchant.id,
            url=merchant.webhook_url,
            payload=payload_bytes.decode("utf-8"),
            signature=sign_payload(payload_bytes, settings.merchant_webhook_secret),
        )
        db.add(delivery)
        await db.commit()
        await db.refresh(delivery)
        deliver_merchant_webhook.delay(
            delivery.id, delivery.url, delivery.payload, settings.merchant_webhook_secret
        )

    logger.info(
        "bank_callback_accepted",
        callback_event=event,
        psp_reference=psp_reference,
        replay=result.get("replay", False),
    )
    return WebhookAck(accepted=True)