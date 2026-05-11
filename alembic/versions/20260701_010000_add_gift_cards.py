"""Add gift_cards + gift_card_transactions — Phase 8.3.

Revision ID: gift_cards_20260701
Revises: inventory_multiloc_20260615
Create Date: 2026-07-01

Phase 8.3 of the Shopify-parity roadmap. Two tables for gift cards
as **tender** (a payment method), not discount coupons.

Schema decisions:
* `code_hash` stored, not plaintext — customer-typed code is
  SHA-256'd before lookup. Last four chars kept in plaintext for
  hub display.
* `current_balance_cents` denormalized alongside the
  `gift_card_transactions` ledger (whose SUM is authoritative) so
  checkout-redemption can read balance without a SUM().
* Partial indexes on `customer_id IS NOT NULL` and on `expires_at
  IS NOT NULL AND status = 'active'` keep the hot-path indexes
  small (most cards have neither field set).

No backfill — gift cards are net-new in Phase 8.3.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "gift_cards_20260701"
down_revision: str | None = "inventory_multiloc_20260615"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Status + transaction-kind enums ────────────────────────
    gift_card_status = sa.Enum(
        "active",
        "depleted",
        "expired",
        "voided",
        name="giftcardstatus",
    )
    gift_card_status.create(op.get_bind(), checkfirst=True)

    gift_card_tx_kind = sa.Enum(
        "issue",
        "redeem",
        "refund",
        "adjust",
        "void",
        name="giftcardtransactionkind",
    )
    gift_card_tx_kind.create(op.get_bind(), checkfirst=True)

    # ── 2. gift_cards ─────────────────────────────────────────────
    op.create_table(
        "gift_cards",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False),
        sa.Column("initial_balance_cents", sa.Integer, nullable=False),
        sa.Column("current_balance_cents", sa.Integer, nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="EGP",
        ),
        sa.Column(
            "status",
            gift_card_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "issued_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("issuing_order_id", UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("code_hash", name="uq_gift_cards_code_hash"),
        schema="public",
    )
    op.create_index(
        "ix_gift_cards_tenant",
        "gift_cards",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_gift_cards_store",
        "gift_cards",
        ["store_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_gift_cards_customer",
        "gift_cards",
        ["customer_id"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    op.create_index(
        "ix_gift_cards_expires_at",
        "gift_cards",
        ["expires_at"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("expires_at IS NOT NULL AND status = 'active'"),
    )

    # ── 3. gift_card_transactions ─────────────────────────────────
    op.create_table(
        "gift_card_transactions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "gift_card_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.gift_cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", gift_card_tx_kind, nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("order_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "ix_gc_tx_tenant",
        "gift_card_transactions",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_gc_tx_card",
        "gift_card_transactions",
        ["gift_card_id", "created_at"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_gc_tx_order",
        "gift_card_transactions",
        ["order_id"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gc_tx_order", table_name="gift_card_transactions", schema="public"
    )
    op.drop_index("ix_gc_tx_card", table_name="gift_card_transactions", schema="public")
    op.drop_index(
        "ix_gc_tx_tenant", table_name="gift_card_transactions", schema="public"
    )
    op.drop_table("gift_card_transactions", schema="public")

    op.drop_index("ix_gift_cards_expires_at", table_name="gift_cards", schema="public")
    op.drop_index("ix_gift_cards_customer", table_name="gift_cards", schema="public")
    op.drop_index("ix_gift_cards_store", table_name="gift_cards", schema="public")
    op.drop_index("ix_gift_cards_tenant", table_name="gift_cards", schema="public")
    op.drop_table("gift_cards", schema="public")

    sa.Enum(name="giftcardtransactionkind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="giftcardstatus").drop(op.get_bind(), checkfirst=True)
