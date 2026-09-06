"""Pydantic schemas for webhook callbacks and verification meta."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class BankWebhookCallback(BaseModel):
    """Payload dispatched by a PSP/bank to the webhook endpoint.

    The raw body (exact bytes) is used for HMAC verification, so APIs always
    take the body as ``bytes`` and parse it separately.
    """

    event: str = Field(pattern=r"^payment\.(success|failed)$")
    psp_reference: str
    transaction_id: str
    amount: Decimal = Field(..., gt=Decimal("0"))
    currency: str = Field(min_length=3, max_length=3)
    vpa: str | None = None


class WebhookAck(BaseModel):
    accepted: bool = True
    reason: str | None = None