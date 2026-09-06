"""Initial schema for the payment gateway.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _money() -> sa.Numeric:
    return sa.Numeric(18, 4)


def upgrade() -> None:
    # ---------- merchants ----------------------------------------------------
    op.create_table(
        "merchants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("webhook_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_merchants_is_active", "merchants", ["is_active"])

    # ---------- payment_intents ------------------------------------------------
    op.create_table(
        "payment_intents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", _money(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="CREATED"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payment_intents_merchant_id", "payment_intents", ["merchant_id"])
    op.create_index("ix_payment_intents_public_id", "payment_intents", ["public_id"], unique=True)
    op.create_index("ix_payment_intents_status", "payment_intents", ["status"])
    op.create_index("ix_payment_intents_idempotency_key", "payment_intents", ["idempotency_key"], unique=True)

    # ---------- transactions -----------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("intent_id", sa.Uuid(), sa.ForeignKey("payment_intents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vpa", sa.String(length=255), nullable=False),
        sa.Column("payment_method", sa.String(length=20), nullable=False, server_default="upi"),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="INITIATED"),
        sa.Column("psp_reference", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_transactions_intent_id", "transactions", ["intent_id"])
    op.create_index("ix_transactions_psp_reference", "transactions", ["psp_reference"])
    op.create_index("ix_transactions_public_id", "transactions", ["public_id"], unique=True)
    op.create_index("ix_transactions_state", "transactions", ["state"])
    op.create_index("ix_transactions_vpa", "transactions", ["vpa"])

    # ---------- upi_vpas ----------------------------------------------------------
    op.create_table(
        "upi_vpas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vpa", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_upi_vpas_merchant_id", "upi_vpas", ["merchant_id"])
    op.create_index("ix_upi_vpas_vpa", "upi_vpas", ["vpa"])

    # ---------- ledger account type + accounts ------------------------------------
    account_type_enum = sa.Enum(
        "VIRTUAL", "MERCHANT_SETTLEMENT", "PLATFORM_FEE", name="account_type"
    )
    entry_type_enum = sa.Enum("DEBIT", "CREDIT", name="entry_type")
    account_type_enum.create(op.get_bind(), checkfirst=True)
    entry_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("account_type", account_type_enum, nullable=False),
        sa.Column("identifier", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("balance", _money(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("identifier", "currency", name="uq_acct_currency"),
    )
    op.create_index("ix_ledger_accounts_account_type", "ledger_accounts", ["account_type"])
    op.create_index("ix_ledger_accounts_identifier", "ledger_accounts", ["identifier"])
    op.create_index("ix_ledger_accounts_merchant_id", "ledger_accounts", ["merchant_id"])

    # ---------- ledger_entries -----------------------------------------------------
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", _money(), nullable=False),
        sa.Column("entry_type", entry_type_enum, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ledger_entries_account_id", "ledger_entries", ["account_id"])
    op.create_index("ix_ledger_entries_entry_type", "ledger_entries", ["entry_type"])
    op.create_index("ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"])

    # ---------- webhook_deliveries ---------------------------------------------------
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("transaction_id", sa.Uuid(), sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("signature", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_webhook_deliveries_merchant_id", "webhook_deliveries", ["merchant_id"])
    op.create_index("ix_webhook_deliveries_state", "webhook_deliveries", ["state"])
    op.create_index("ix_webhook_deliveries_transaction_id", "webhook_deliveries", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("ledger_entries")
    op.drop_table("ledger_accounts")
    op.drop_table("upi_vpas")
    op.drop_table("transactions")
    op.drop_table("payment_intents")
    op.drop_table("merchants")
    op.execute("DROP TYPE IF EXISTS account_type")
    op.execute("DROP TYPE IF EXISTS entry_type")