"""Celery task definitions for resiliency: webhook delivery + reconciliation.

Webhook delivery retries use exponential backoff with jitter so a thundering
herd of retries is flattened across time. ``acks_late`` + ``reject_on_worker_lost``
(configured on the app) guarantee no notification is lost if a worker dies.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import sign_payload
from app.models import PaymentIntent, PaymentStatus
from app.workers.celery_app import celery_app

logger = get_logger("workers")


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter: ``base * 2**(attempt-1) + rand(0, base)``."""
    base = settings.celery_webhook_retry_backoff
    delay = base * (2 ** (attempt - 1)) + random.uniform(0, base)
    return min(3600, delay)


@celery_app.task(
    name="app.workers.tasks.deliver_merchant_webhook",
    bind=True,
    max_retries=settings.celery_webhook_max_retries,
)
def deliver_merchant_webhook(
    self: Any,
    delivery_id: int,
    url: str,
    payload: str,
    secret: str,
) -> str:
    """Deliver a signed webhook payload to a merchant endpoint with retries.

    Retries honour the configured max, using exponential backoff + jitter.
    Non-2xx responses and network errors raise to trigger a retry.
    """
    attempt = self.request.retries + 1
    body = payload.encode("utf-8")
    signature = sign_payload(body, secret)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Event": json.loads(payload).get("event", ""),
    }

    try:
        resp = httpx.post(url, content=body, headers=headers, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "merchant_webhook_attempt_failed",
            delivery_id=delivery_id,
            attempt=attempt,
            url=url,
            error=str(exc),
        )
        raise self.retry(
            exc=exc,
            countdown=_backoff_delay(attempt),
            max_retries=settings.celery_webhook_max_retries,
        ) from exc

    logger.info(
        "merchant_webhook_delivered",
        delivery_id=delivery_id,
        url=url,
        attempt=attempt,
        status_code=resp.status_code,
    )
    return resp.text


async def _reconcile_stuck_intents_async() -> list[int]:
    """Sweep intents stuck in PROCESSING past the threshold and flag them."""
    from app.db.session import async_session_factory

    threshold = datetime.now(UTC) - timedelta(hours=1)
    async with async_session_factory() as session:
        result = await session.execute(
            select(PaymentIntent.id, PaymentIntent.public_id)
            .where(
                PaymentIntent.status == PaymentStatus.PROCESSING.value,
                PaymentIntent.updated_at < threshold,
            )
            .limit(500)
        )
        rows = result.all()
        for intent_id, public_id in rows:
            # Real systems enqueue a PSP status-check here instead of
            # force-transitioning, protecting against double settlement.
            logger.warning(
                "intent_stuck_in_processing",
                intent_id=intent_id,
                order_id=public_id,
            )
        return [row[0] for row in rows]


@celery_app.task(name="app.workers.tasks.reconcile_stuck_intents")
def reconcile_stuck_intents() -> dict[str, int]:
    """Celery beat task: flag intents stuck in PROCESSING beyond the threshold."""
    logger.info("reconciliation_walk_started")
    flagged = asyncio.run(_reconcile_stuck_intents_async())
    logger.info("reconciliation_walk_finished", pending_intents=len(flagged))
    return {"flagged": len(flagged)}