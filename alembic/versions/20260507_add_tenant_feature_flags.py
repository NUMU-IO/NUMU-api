"""Add `feature_flags` JSONB column to `tenants`.

Used by the offers-v2 rollout (step 14 §2) to gate the new
`/promotions` endpoints, the storefront's `/active` fetch, and the
merchant onboarding tour per-tenant. Default `{}` means "all flags
off"; the rollout flips them in waves via the admin UI / direct
update.

Revision ID: tenant_feature_flags_20260507
Revises: promo_daily_20260507
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "tenant_feature_flags_20260507"
down_revision: str | None = "promo_daily_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "feature_flags",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("tenants", "feature_flags", schema="public")
