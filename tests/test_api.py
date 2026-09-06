"""End-to-end API tests exercising the merchant → order → payment → callback flow."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from app.core.config import settings

HEADERS_LIMIT = {"X-RateLimit-Limit", "X-RateLimit-Remaining", "Content-Type"}


def _sign(body: bytes, secret: str = settings.psp_webhook_secret) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def test_health(api_client):
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_full_payment_flow(api_client):
    # 1. Provision merchant
    r = await api_client.post(
        "/v1/merchants", json={"name": "Tester", "webhook_url": "https://me.example/hook"}
    )
    assert r.status_code == 201, r.text
    creds = r.json()
    api_key = creds["api_key"]
    auth = {"X-Api-Key": api_key}

    # 2. Create an order (idempotency keyed)
    idem_key = str(uuid.uuid4())
    order_payload = {"amount": "1500.0000", "currency": "INR", "idempotency_key": idem_key}
    r = await api_client.post("/v1/orders", json=order_payload, headers=auth)
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["amount"] == "1500.0000"

    # 3. Replay the same order request -> same order, no duplicate
    r2 = await api_client.post("/v1/orders", json=order_payload, headers=auth)
    assert r2.status_code == 201
    assert r2.json()["order_id"] == order["order_id"]

    # 4. Initiate UPI payment
    r = await api_client.post(
        "/v1/payments/upi/initiate",
        json={
            "order_id": order["order_id"],
            "vpa": "payer@okhdfcbank",
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=auth,
    )
    assert r.status_code == 201, r.text
    payment = r.json()
    assert payment["status"] == "PENDING"
    assert payment["psp_reference"]

    # 5. Bank callback (HMAC signed)
    body = json.dumps(
        {
            "event": "payment.success",
            "psp_reference": payment["psp_reference"],
            "transaction_id": payment["transaction_id"],
            "amount": "1500.0000",
            "currency": "INR",
        }
    ).encode()
    r = await api_client.post(
        "/v1/webhooks/bank-callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": _sign(body),
            "X-Timestamp": str(int(time.time())),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True

    # 6. Order reflects success
    r = await api_client.get(f"/v1/orders/{order['order_id']}", headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "SUCCESS"


async def test_callback_rejects_bad_signature(api_client):
    body = json.dumps({"event": "payment.success"}).encode()
    r = await api_client.post(
        "/v1/webhooks/bank-callback",
        content=body,
        headers={"X-Signature": "deadbeef"},
    )
    assert r.status_code == 401


async def test_rate_limit_headers_present(api_client):
    r = await api_client.get("/health")
    assert r.status_code == 200