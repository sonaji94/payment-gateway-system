# Architecture — Payment Gateway System

## 1. Tech stack

| Concern               | Choice                                    | Why                                                     |
|-----------------------|-------------------------------------------|---------------------------------------------------------|
| API                   | FastAPI (ASGI)                            | Async IOPS, pydantic-era validation, typed OpenAPI      |
| Database / ORM        | PostgreSQL 16 + SQLAlchemy 2.0 (asyncpg)  | AsyncEngine, `Mapped`/`mapped_column`, native UUID      |
| Validation            | Pydantic v2                               | `Decimal` money, strict schemas, dependency-free models |
| Cache / distrib. lock | Redis (redis-py)                          | Idempotency keys, rate-limit counters, Celery broker    |
| Workers               | Celery 5 (broker: Redis)                  | `acks_late` webhook delivery, beat reconciliation       |
| Security              | OAuth2 + JWT, HMAC-SHA256, salted hashing | Credential + signature integrity                        |
| Logging / audit       | structlog (JSON in prod)                  | Machine-readable audit trail per request                |

## 2. Request lifecycle

```
Merchant API                    Gateway                              PSP / Bank
   │  POST /v1/orders              │                                     │
   ├──────────────────────────────►│ ── Idempotency key (Redis SET NX)   │
   │                               │ ── CREATE PaymentIntent (CREATED)   │
   │◄──────────────────────────────┤                                     │
   │  POST /v1/payments/upi/initiate│                                    │
   ├──────────────────────────────►│ ── SELECT FOR UPDATE intent         │
   │                               │ ── CREATED -> PROCESSING            │
   │                               │ ── create Transaction (PENDING)     │
   │◄──────────────────────────────┤ ───────────────────────────────────►│ UPI Collect / Intent
   │                               │                                     │
   │                               │◄────────── bank-callback (HMAC) ────┤
   │                               │ ── verify HMAC-SHA256 + timestamp    │
   │                               │ ── SELECT FOR UPDATE txn + intent    │
   │                               │ ── PENDING -> SUCCESS/FAILED         │
   │                               │ ── post ledger DEBIT+CREDIT          │
   │                               │ ── outbox row + Celery dispatch      │
   │◄──────── merchant webhook ────┤ (JSON, X-Webhook-Signature)          │
```

## 3. Concurrency & money safety

- **Pessimistic locking** — every state advance uses `SELECT ... FOR UPDATE`
  on the `PaymentIntent` row, so concurrent UPI initiations / duplicate bank
  callbacks serialize on the database row (see `app/services/webhook.py`).
- **Exactly-once ledger post** — state transition + ledger entries commit in a
  single transaction; a replayed `payment.success` callback is detected
  (`transaction.state == SUCCESS`) and short-circuited without re-posting.
- **Idempotency** — `Idempotency-Key` header is reserved atomically in Redis
  (`SET NX`). Concurrent retries get `409`; post-completion retries get the
  cached response. Failed requests free the key for safe retry.
- **Money type** — all monetary columns are `Numeric(18, 4)` mapped to Python
  `Decimal`. No floats near money.

## 4. Double-entry ledger model

Accounts live in `ledger_accounts` with type + unique `(identifier, currency)`.
On `payment.success`:

```
DEBIT   virtual:<vpa>      amounts   (receivable recorded from the payer)
CREDIT  settlement:<merchant>  +amount   (payable accrued to the merchant)
```

Both sides post as immutable `ledger_entries` rows in the same transaction as
the state change. Settlement (`workers/tasks.py`) later moves
`MERCHANT_SETTLEMENT` balances toward a bank payout with the mirroring
`DEBIT settlement / CREDIT cash`.

## 5. Webhooks & resiliency

- **Inbound (bank → gateway):** `X-Signature` = HMAC-SHA256 over the raw body
  with the PSP shared secret; `X-Timestamp` freshness window (default 300s)
  rejects replays. Returns `2xx ack` immediately after durable processing.
- **Outbound (gateway → merchant):** durable `webhook_deliveries` outbox row is
  written before Celery dispatch. `deliver_merchant_webhook` retries with
  exponential backoff + jitter (max 5 attempts, capped at 1h). Middleware
  settings: `acks_late`, `reject_on_worker_lost`.
- **Reconciliation:** `reconcile_stuck_intents` (Celery beat, every 15m) flags
  intents stuck in `PROCESSING`, pushing references for PSP status checks.

## 6. Security posture

- API keys & secrets stored only as salted SHA-256 digests (`derive_key`).
- Short-lived JWTs (`HS256`, exp) for server-to-server calls; the OAuth2
  password form endpoint exchanges key+secret for a token.
- Constant-time comparisons (`hmac.compare_digest`) for all signature checks.
- Secrets never committed; `.env.example` documents placeholders only.

## 7. Scaling notes

- Stateless API tier → horizontal scale behind a load balancer (idempotency &
  rate limits are fully centralized in Redis).
- DB pool sized via settings (`db_pool_size`, `db_max_overflow`); tune per
  instance to saturate PG connections, not starve them.
- Webhook fan-out is decoupled through Celery — the API never blocks on the
  merchant; throughput ceilings are set by the broker, not the API.