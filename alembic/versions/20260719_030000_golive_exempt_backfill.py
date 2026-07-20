"""Grandfather existing tenants out of the go-live gate.

New business rule: a NEW merchant can build their store freely, but the
storefront cannot take orders ("go live") until the tenant picks a paid
plan or Pay as you Grow. Enforcement reads
``tenant.feature_flags["golive_exempt"]`` — tenants carrying the flag
are never gated.

This migration stamps the flag on EVERY tenant that exists at deploy
time, so the gate only ever applies to signups created after this
revision ran. Purely a data backfill; downgrade removes the flag.

Revision ID: golive_exempt_20260719
Revises: merchant_wallet_v2_20260719
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "golive_exempt_20260719"
down_revision: str | Sequence[str] | None = "merchant_wallet_v2_20260719"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE public.tenants "
        "SET feature_flags = COALESCE(feature_flags, '{}'::jsonb) "
        "|| '{\"golive_exempt\": true}'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE public.tenants SET feature_flags = feature_flags - 'golive_exempt' "
        "WHERE feature_flags ? 'golive_exempt'"
    )
