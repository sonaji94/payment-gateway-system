# Payment Gateway System

A production-grade, high-throughput payment gateway backend (Razorpay/Google Pay UPI style) built on **Python 3.12+, FastAPI (ASGI), PostgreSQL (asyncpg), Redis**, and **Celery**.

## Highlights

- **FastAPI async API** — `/v1/orders`, `/v1/payments/upi/initiate`, `/v1/webhooks/bank-callback`
- **ACID double-entry ledger** — `Numeric(18,4)` amounts, DEBIT/CREDIT balancing, pessimistic locking (`SELECT ... FOR UPDATE`)
- **Idempotency** — Redis-backed `Idempotency-Key` middleware prevents double-debiting on client retries
- **Security** — OAuth2 + JWT, API-key + secret credential hashing, HMAC-SHA256 webhook signature verification with replay protection
- **Resiliency** — Celery worker with `acks_late`, exponential-backoff webhook retries, and scheduled reconciliation/settlement

## Quick start

```bash
docker compose up --build        # db + redis + api + worker + beat
open http://localhost:8000/docs
```

### Local (no Docker)

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -e ".[dev]"
copy .env.example .env                           # set DATABASE_URL & secrets
alembic upgrade head
uvicorn app.main:app --reload
celery -A app.workers.celery_app worker --loglevel=info
celery -A app.workers.celery_app beat --loglevel=info
```

## Repository layout

```
app/
  core/        config, security (JWT/HMAC), logging, redis
  db/          async engine, session, declarative Base (UUID pk)
  models/      Merchant, PaymentIntent, Transaction, UpiVpa,
               LedgerAccount, LedgerEntry, WebhookDelivery
  schemas/     Pydantic v2 request/response models
  api/v1/      merchants, orders, payments, webhooks routers
  middleware/  idempotency (Redis lock), rate-limit, request-id
  services/    order, payment (state machine), webhook, ledger
  workers/     Celery app + webhook-delivery / settlement tasks
alembic/       async migrations
```

## API overview

| Method | Endpoint                       | Auth            | Purpose                          |
|--------|--------------------------------|-----------------|----------------------------------|
| POST   | `/v1/merchants`                | —               | Provision merchant, get key/secret |
| POST   | `/v1/merchants/token`          | OAuth2 form     | Exchange key+secret for a JWT     |
| POST   | `/v1/orders`                   | Bearer/API key  | Create an order intent            |
| GET    | `/v1/orders/{order_id}`        | Bearer/API key  | Fetch order status                |
| POST   | `/v1/payments/upi/initiate`    | Bearer/API key  | Initiate UPI Collect              |
| POST   | `/v1/webhooks/bank-callback`   | HMAC-SHA256     | PSP callback (signed)             |

### Callback signing (for the bank/PSP side)

```python
import hashlib, hmac, json
secret = "change-me-psp-webhook-secret"
body = json.dumps({"event":"payment.success","psp_reference":"psp_...", ...}).encode()
sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
# POST /v1/webhooks/bank-callback  Headers: X-Signature: sig, X-Timestamp: <unix>
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.