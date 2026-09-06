"""Pydantic schemas for merchant lifecycle & credential provisioning."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class MerchantCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    webhook_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Merchant endpoint that receives signed payment webhooks.",
    )


class MerchantCreateResponse(BaseModel):
    merchant_id: uuid.UUID
    api_key: str
    api_secret: str
    note: str = "Store these credentials securely. They are shown only once."