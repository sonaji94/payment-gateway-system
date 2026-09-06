"""Security primitives: JWT (OAuth2), HMAC-SHA256, and credential hashing.

All secrets are stored as hashes — never as plaintext. API keys / secrets are
HMAC-derived with a global salt so a leaked database does not leak usable
credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from jose import JWTError, jwt

from app.core.config import settings


# --------------------------------------------------------------------------- #
# Credential hashing
# --------------------------------------------------------------------------- #
def derive_key(token: str) -> str:
    """Return a salted, SHA-256 derived hash of a raw API key or secret."""
    salted = f"{settings.api_key_salt}:{token}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


def generate_api_credentials() -> tuple[str, str]:
    """Generate fresh (api_key, api_secret) plaintext pair.

    The caller must return the plaintext pair to the merchant exactly once and
    persist only ``derive_key(...)`` values.
    """
    return secrets.token_urlsafe(32), secrets.token_urlsafe(48)


def secure_compare(left: str, right: str) -> bool:
    """Constant-time comparison of two strings."""
    return hmac.compare_digest(left, right)


def verify_credential(plaintext: str, stored_hash: str) -> bool:
    return secure_compare(derive_key(plaintext), stored_hash)


# --------------------------------------------------------------------------- #
# JWT (OAuth2 bearer)
# --------------------------------------------------------------------------- #
def create_access_token(subject: str, merchant_id: int) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "mid": merchant_id,
        "exp": expires,
        "iat": now,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT, raising on invalid/expired tokens."""
    try:
        return jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise ValueError(f"Invalid or expired token: {exc}") from exc


# --------------------------------------------------------------------------- #
# HMAC-SHA256 webhook signature
# --------------------------------------------------------------------------- #
def sign_payload(payload: bytes, secret: str) -> str:
    """Compute an ``HMAC-SHA256`` hex digest over a raw webhook body."""
    return hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()


def verify_signature(payload: bytes, secret: str, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature in constant time."""
    expected = sign_payload(payload, secret)
    return secure_compare(expected, signature)


# --------------------------------------------------------------------------- #
# Random idempotency / tracking ids
# --------------------------------------------------------------------------- #
def new_id(prefix: str, nbytes: int = 16) -> str:
    """Generate a URL-safe opaque id with a human-friendly prefix."""
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


ApiScope = Literal["read", "write", "webhook"]
