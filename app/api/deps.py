"""FastAPI dependencies: auth (JWT + API-key) and DB session."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, derive_key
from app.db.session import get_db_session
from app.models import Merchant

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/merchants/token", auto_error=False)
api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)


async def get_current_merchant(
    db: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    api_key: Annotated[str | None, Depends(api_key_header)] = None,
) -> Merchant:
    """Resolve the authenticated merchant from either a JWT or API key.

    JWT is preferred when present; otherwise the ``X-Api-Key`` header is used
    and looked up via its salted hash.
    """
    merchant: Merchant | None = None

    if token:
        try:
            payload = decode_access_token(token)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        merchant_id = payload.get("mid")
        if merchant_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
        merchant = await db.get(Merchant, merchant_id)

    elif api_key is not None:
        key_hash = derive_key(api_key)
        result = await db.execute(
            select(Merchant).where(Merchant.api_key_hash == key_hash)
        )
        merchant = result.scalar_one_or_none()

    if merchant is None or not merchant.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing credentials",
        )

    return merchant


MerchantDep = Annotated[Merchant, Depends(get_current_merchant)]