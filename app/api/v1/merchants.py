"""Merchant provisioning: create merchant, exchange credentials for a JWT."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import create_access_token, derive_key, generate_api_credentials
from app.models import Merchant
from app.schemas.merchant import MerchantCreateRequest, MerchantCreateResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/merchants", tags=["merchants"])


@router.post(
    "",
    response_model=MerchantCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_merchant(
    payload: MerchantCreateRequest, db: DbSession
) -> MerchantCreateResponse:
    """Provision a merchant and return one-time plaintext credentials.

    Only salted hashes are stored; the plaintext key/secret are shown exactly
    once in the response and must be persisted securely by the caller.
    """
    api_key, api_secret = generate_api_credentials()
    merchant = Merchant(
        name=payload.name,
        api_key_hash=derive_key(api_key),
        secret_hash=derive_key(api_secret),
        is_active=True,
        webhook_url=payload.webhook_url,
    )
    db.add(merchant)
    await db.commit()
    await db.refresh(merchant)

    logger.info("merchant_created", merchant_id=merchant.id)
    return MerchantCreateResponse(
        merchant_id=merchant.id, api_key=api_key, api_secret=api_secret
    )


@router.post("/token")
async def merchant_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
):
    """Exchange API key/secret for a short-lived JWT access token.

    Mirrors the OAuth2 password flow but with gateway credentials as the
    username/password pair, so `/docs` supports it out of the box.
    """
    from app.core.security import verify_credential

    result = await db.execute(
        select(Merchant).where(Merchant.api_key_hash == derive_key(form_data.username))
    )
    merchant = result.scalar_one_or_none()

    if merchant is None or not verify_credential(form_data.password, merchant.secret_hash):
        logger.warning("merchant_auth_failed", username_hint=form_data.username[:8])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect API key or secret",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(subject=str(merchant.id), merchant_id=merchant.id)
    logger.info("merchant_token_issued", merchant_id=merchant.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }