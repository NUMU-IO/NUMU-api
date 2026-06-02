"""Add country (ISO 3166-1 alpha-2) market discriminator to stores.

Revision ID: add_store_country_20260602
Revises: rich_wa_templates_20260601
Create Date: 2026-06-02

Phase 0 of multi-market support. Adds a ``country`` column to
``public.stores`` so every store declares the market it operates in.
This drives the tax jurisdiction (14% EG VAT vs 15% SA VAT + ZATCA),
the default currency/language at onboarding, and the payment-gateway
allow-list — all resolved through ``market_registry.py``.

``server_default='EG'`` backfills every existing store to Egypt (the
v1 launch market) so the NOT NULL constraint holds for current rows
without a separate data-migration pass. The column is a plain 2-char
string, not an enum, so onboarding a new market is a registry change
rather than a DB migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_store_country_20260602"
down_revision: str = "rich_wa_templates_20260601"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stores",
        sa.Column(
            "country",
            sa.String(length=2),
            nullable=False,
            server_default="EG",
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("stores", "country", schema="public")
