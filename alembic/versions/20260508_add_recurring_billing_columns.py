"""Add recurring billing columns to tenants (backend-005).

Revision ID: recurring_billing_20260508
Revises: shopify_subs_20260508
Create Date: 2026-05-08

Adds:
  * `paymob_card_token_encrypted` — TEXT, holds the encrypted card
    token used by `PaymobPaymentService.charge_saved_token` for
    monthly/annual renewals.
  * `renewal_retry_count` — INT default 0; incremented on each
    consecutive renewal failure. Reset to 0 on successful charge or
    cancel. After 3 failures + ≥72h elapsed the renewal task moves
    the tenant to READ_ONLY.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "recurring_billing_20260508"
down_revision: str | None = "shopify_subs_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("paymob_card_token_encrypted", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column(
            "renewal_retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="public",
    )
    op.create_index(
        "ix_tenants_next_renewal_at_active",
        "tenants",
        ["next_renewal_at"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("lifecycle_state IN ('active', 'past_due')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tenants_next_renewal_at_active",
        table_name="tenants",
        schema="public",
    )
    op.drop_column("tenants", "renewal_retry_count", schema="public")
    op.drop_column("tenants", "paymob_card_token_encrypted", schema="public")
