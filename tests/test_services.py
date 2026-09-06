"""Service-level tests: order creation, UPI initiation, bank callback."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import derive_key, new_id
from app.models import (
    EntryType,
    LedgerAccount,
    LedgerEntry,
    Merchant,
    PaymentIntent,
    PaymentStatus,
    Transaction,
    TransactionState,
)
from app.schemas.payment import CreateOrderRequest, UpiInitiateRequest
from app.services.payment import AlreadyFinalised, initiate_upi
from app.services.webhook import AmountMismatch, process_bank_callback


async def _make_merchant(db_session) -> Merchant:
    merchant = Merchant(
        name="Acme",
        api_key_hash=derive_key(new_id("key")),
        secret_hash=derive_key(new_id("sec")),
        is_active=True,
        webhook_url="https://acme.example/hook",
    )
    db_session.add(merchant)
    await db_session.commit()
    await db_session.refresh(merchant)
    return merchant


async def _make_order(db_session, merchant, amount="1250.0000") -> PaymentIntent:
    intent = PaymentIntent(
        public_id=new_id("ord"),
        merchant_id=merchant.id,
        amount=Decimal(amount),
        currency="INR",
        idempotency_key=new_id("idem"),
        status=PaymentStatus.CREATED.value,
    )
    db_session.add(intent)
    await db_session.commit()
    await db_session.refresh(intent)
    return intent


async def _make_pending_txn(db_session, intent) -> Transaction:
    txn = Transaction(
        public_id=new_id("txn"),
        intent_id=intent.id,
        vpa="payer@upi",
        payment_method="upi",
        state=TransactionState.PENDING.value,
        psp_reference=new_id("psp"),
    )
    intent.status = PaymentStatus.PROCESSING.value
    db_session.add(txn)
    await db_session.commit()
    await db_session.refresh(txn)
    return txn


async def test_create_order_and_idempotent_replay(db_session):
    merchant = await _make_merchant(db_session)
    req = CreateOrderRequest(
        amount=Decimal("100.0000"),
        idempotency_key=new_id("idem"),
        description="test",
    )
    from app.services.order import create_order

    resp1 = await create_order(db_session, merchant, req)
    resp2 = await create_order(db_session, merchant, req)
    assert resp1.order_id == resp2.order_id
    assert resp1.amount == resp2.amount == Decimal("100.0000")


async def test_initiate_upi_advances_state(db_session):
    merchant = await _make_merchant(db_session)
    intent = await _make_order(db_session, merchant)
    req = UpiInitiateRequest(
        idempotency_key=new_id("idem"), order_id=intent.public_id, vpa="payer@upi"
    )
    resp = await initiate_upi(db_session, merchant, req)
    assert resp.status == TransactionState.PENDING.value
    await db_session.refresh(intent)
    assert intent.status == PaymentStatus.PROCESSING.value


async def test_initiate_upi_refuses_finalised_order(db_session):
    merchant = await _make_merchant(db_session)
    intent = await _make_order(db_session, merchant)
    intent.status = PaymentStatus.SUCCESS.value
    await db_session.commit()
    req = UpiInitiateRequest(
        idempotency_key=new_id("idem"), order_id=intent.public_id, vpa="payer@upi"
    )
    with pytest.raises(AlreadyFinalised):
        await initiate_upi(db_session, merchant, req)


async def test_bank_callback_success_posts_ledger(db_session):
    merchant = await _make_merchant(db_session)
    intent = await _make_order(db_session, merchant)
    txn = await _make_pending_txn(db_session, intent)

    result = await process_bank_callback(
        db_session, "payment.success", txn.psp_reference, Decimal("1250.0000")
    )
    assert result["status"] == PaymentStatus.SUCCESS.value

    await db_session.refresh(txn)
    await db_session.refresh(intent)
    assert txn.state == TransactionState.SUCCESS.value
    assert intent.status == PaymentStatus.SUCCESS.value

    entries = (
        await db_session.execute(
            select(LedgerEntry).where(LedgerEntry.transaction_id == txn.id)
        )
    ).scalars().all()
    assert {(e.entry_type.value, e.amount) for e in entries} == {
        (EntryType.DEBIT.value, Decimal("1250.0000")),
        (EntryType.CREDIT.value, Decimal("1250.0000")),
    }

    accounts = (await db_session.execute(select(LedgerAccount))).scalars().all()
    by_id = {a.identifier: a for a in accounts}
    assert by_id["payer@upi"].balance == Decimal("1250.0000")
    assert by_id[f"merchant:{merchant.id}"].balance == Decimal("1250.0000")


async def test_bank_callback_replay_is_idempotent(db_session):
    merchant = await _make_merchant(db_session)
    intent = await _make_order(db_session, merchant)
    txn = await _make_pending_txn(db_session, intent)

    first = await process_bank_callback(
        db_session, "payment.success", txn.psp_reference, Decimal("1250.0000")
    )
    assert first["replay"] is False

    entries_after_first = (
        await db_session.execute(
            select(LedgerEntry).where(LedgerEntry.transaction_id == txn.id)
        )
    ).scalars().all()
    assert len(entries_after_first) == 2

    second = await process_bank_callback(
        db_session, "payment.success", txn.psp_reference, Decimal("1250.0000")
    )
    assert second["replay"] is True

    entries_after_second = (
        await db_session.execute(
            select(LedgerEntry).where(LedgerEntry.transaction_id == txn.id)
        )
    ).scalars().all()
    assert len(entries_after_second) == 2  # no double posting


async def test_bank_callback_amount_mismatch(db_session):
    merchant = await _make_merchant(db_session)
    intent = await _make_order(db_session, merchant)
    txn = await _make_pending_txn(db_session, intent)
    with pytest.raises(AmountMismatch):
        await process_bank_callback(
            db_session, "payment.success", txn.psp_reference, Decimal("1.0000")
        )