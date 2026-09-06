"""Payment state machine: valid transitions and guard helpers.

Transitions are enforced at the service layer inside a single database
transaction that uses ``SELECT ... FOR UPDATE`` so concurrent workers cannot
double-apply a state change.
"""

from __future__ import annotations

from typing import TypeVar

from app.models import PaymentStatus, TransactionState

TModel = TypeVar("TModel")

# Permission matrix for the *external, observable* intent status.
PAYMENT_INTENT_TRANSITIONS: dict[PaymentStatus, set[PaymentStatus]] = {
    PaymentStatus.CREATED: {PaymentStatus.PROCESSING},
    PaymentStatus.PROCESSING: {PaymentStatus.SUCCESS, PaymentStatus.FAILED},
    PaymentStatus.SUCCESS: set(),
    PaymentStatus.FAILED: set(),
}

# Internal transaction-level transitions are a linear pipeline.
TRANSACTION_TRANSITIONS: dict[TransactionState, set[TransactionState]] = {
    TransactionState.INITIATED: {TransactionState.PENDING},
    TransactionState.PENDING: {TransactionState.SUCCESS, TransactionState.FAILED},
    TransactionState.SUCCESS: set(),
    TransactionState.FAILED: set(),
}


class InvalidStateTransition(ValueError):
    """Raised when a state change is not permitted by the machine."""


def assert_intent_transition(
    current: str | PaymentStatus, target: str | PaymentStatus
) -> None:
    """Validate an intent status transition; raise otherwise."""
    current_s = PaymentStatus(current)
    target_s = PaymentStatus(target)
    if target_s not in PAYMENT_INTENT_TRANSITIONS.get(current_s, set()):
        raise InvalidStateTransition(
            f"Illegal payment intent transition {current_s.value} -> {target_s.value}"
        )


def assert_transaction_transition(
    current: str | TransactionState, target: str | TransactionState
) -> None:
    """Validate a transaction state transition; raise otherwise."""
    current_s = TransactionState(current)
    target_s = TransactionState(target)
    if target_s not in TRANSACTION_TRANSITIONS.get(current_s, set()):
        raise InvalidStateTransition(
            f"Illegal transaction transition {current_s.value} -> {target_s.value}"
        )