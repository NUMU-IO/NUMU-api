"""Paid apps: app subscriptions and the partner ledger.

Revision ID: paid_apps_20260921
Revises: server_checksum_20260920
Create Date: 2026-09-21

Phase 7 of docs/Plans/apps-developer-work. Additive, two new tables:
- ``app_subscriptions``: a store's paid subscription to one app, with the
  price snapshot and the paid period. Deleted with the installation;
- ``partner_ledger_entries``: append-only record of what NUMU owes each
  partner (sales at the partner's 80% share, payouts, adjustments). The
  balance is ``SUM(amount_cents)``.

App charges are wallet entries (``wallet_transactions.kind`` =
``app_charge``); ``kind`` is a plain string column, so no change there.

Downgrade drops both tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "paid_apps_20260921"
down_revision: str | Sequence[str] | None = "server_checksum_20260920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.app_installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("cycle", sa.String(10), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
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
            nullable=False,
        ),
        sa.UniqueConstraint(
            "installation_id", name="uq_app_subscriptions_installation"
        ),
        schema="public",
    )
    op.create_index(
        "ix_app_subscriptions_period_end",
        "app_subscriptions",
        ["status", "current_period_end"],
        schema="public",
    )

    op.create_table(
        "partner_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.partner_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("gross_cents", sa.BigInteger(), nullable=True),
        sa.Column("platform_fee_cents", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.apps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.app_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("reference", sa.String(128), nullable=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_partner_ledger_idempotency"),
        schema="public",
    )
    op.create_index(
        "ix_partner_ledger_partner_created",
        "partner_ledger_entries",
        ["partner_id", "created_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_partner_ledger_partner_created",
        table_name="partner_ledger_entries",
        schema="public",
    )
    op.drop_table("partner_ledger_entries", schema="public")
    op.drop_index(
        "ix_app_subscriptions_period_end",
        table_name="app_subscriptions",
        schema="public",
    )
    op.drop_table("app_subscriptions", schema="public")
