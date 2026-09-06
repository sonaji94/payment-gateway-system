"""Pydantic v2 schemas for payment requests and responses."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class IdempotencyMixin(BaseModel):
    """Every mutating request supports an idempotency key."""

    idempotency_key: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Client-generated idempotency key (UUID or similar).",
    )


class Money(BaseModel):
    """Money using exact decimal (Numeric(18,4)) semantics — no floats."""

    amount: Decimal = Field(..., gt=Decimal("0"), max_digits=18, decimal_places=4)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("amount")
    @classmethod
    def _amount_scale(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.0001"))


class CreateOrderRequest(IdempotencyMixin, Money):
    description: str | None = Field(default=None, max_length=512)


class CreateOrderResponse(BaseModel):
    order_id: str
    idempotency_key: str
    amount: Decimal
    currency: str
    status: str


class UpiInitiateRequest(IdempotencyMixin):
    order_id: str = Field(..., min_length=1, description="Public id of the order.")
    vpa: str = Field(
        ...,
        description="UPI address, e.g. merchant@icici.",
    )

    @field_validator("vpa")
    @classmethod
    def _validate_vpa(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or len(v) < 5 or len(v) > 255:
            raise ValueError("vpa must be a valid UPI address like name@bank")
        return v


class UpiInitiateResponse(BaseModel):
    transaction_id: str
    order_id: str
    status: str
    psp_reference: str | None = None


class OrderStatusResponse(BaseModel):
    order_id: str
    status: str


class WebhookPayload(BaseModel):
    event: Literal["payment.success", "payment.failed"]
    psp_reference: str
    transaction_id: str
    amount: Decimal
    currency: str
    sequence: int | None = None


BankCallbackEvent = Literal["payment.success", "payment.failed"]


class PaymentFlowError(BaseModel):
    code: str
    message: str


class OrdersListResponse(BaseModel):
    orders: list[CreateOrderResponse]


class MoneySummary(BaseModel):
    total: Decimal