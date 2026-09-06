"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import merchants, orders, payments, webhooks

api_v1_router = APIRouter()
api_v1_router.include_router(merchants.router)
api_v1_router.include_router(orders.router)
api_v1_router.include_router(payments.router)
api_v1_router.include_router(webhooks.router)