"""Tests for the payment state machine transition guards."""

from __future__ import annotations

import pytest

from app.models import PaymentStatus, TransactionState
from app.services.state_machine import (
    InvalidStateTransition,
    assert_intent_transition,
    assert_transaction_transition,
)


@pytest.mark.parametrize(
    "current,target",
    [
        (PaymentStatus.CREATED, PaymentStatus.PROCESSING),
        (PaymentStatus.PROCESSING, PaymentStatus.SUCCESS),
        (PaymentStatus.PROCESSING, PaymentStatus.FAILED),
    ],
)
def test_intent_valid_transitions(current, target):
    assert_intent_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (PaymentStatus.CREATED, PaymentStatus.SUCCESS),
        (PaymentStatus.SUCCESS, PaymentStatus.FAILED),
        (PaymentStatus.FAILED, PaymentStatus.PROCESSING),
    ],
)
def test_intent_invalid_transitions(current, target):
    with pytest.raises(InvalidStateTransition):
        assert_intent_transition(current, target)


def test_transaction_transitions():
    assert_transaction_transition(TransactionState.INITIATED, TransactionState.PENDING)
    assert_transaction_transition(TransactionState.PENDING, TransactionState.SUCCESS)
    with pytest.raises(InvalidStateTransition):
        assert_transaction_transition(TransactionState.INITIATED, TransactionState.SUCCESS)