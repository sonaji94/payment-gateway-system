"""Tests for security primitives: JWT, HMAC signing, credential hashing."""

from __future__ import annotations

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    derive_key,
    generate_api_credentials,
    sign_payload,
    verify_credential,
    verify_signature,
)


def test_derive_key_is_deterministic_and_salted():
    token = "sk_live_somesecret"
    assert derive_key(token) == derive_key(token)
    assert len(derive_key(token)) == 64
    assert derive_key(token) != token


def test_generate_credentials_round_trip():
    api_key, api_secret = generate_api_credentials()
    assert verify_credential(api_key, derive_key(api_key))
    assert verify_credential(api_secret, derive_key(api_secret))
    assert not verify_credential("wrong", derive_key(api_secret))


def test_jwt_round_trip():
    token = create_access_token(subject="1", merchant_id=42)
    payload = decode_access_token(token)
    assert payload["mid"] == 42
    assert payload["sub"] == "1"
    assert payload["exp"] > payload["iat"]


def test_hmac_sign_and_verify():
    body = b'{"event": "payment.success"}'
    sig = sign_payload(body, settings.psp_webhook_secret)
    assert verify_signature(body, settings.psp_webhook_secret, sig)
    assert not verify_signature(body, settings.psp_webhook_secret, sig[:-2] + "00")
    assert not verify_signature(b"tampered", settings.psp_webhook_secret, sig)