"""Add trust_network_enabled opt-in column to shopify_app_settings (backend-014).

Revision ID: trust_network_consent_20260509
Revises: recurring_billing_20260508
Create Date: 2026-05-09

Adds a per-store opt-in flag for the cross-merchant trust network.
Default ``true`` — the install-time disclosure modal captures consent.
When false, ``write_network_event`` becomes a no-op for the store,
satisfying the GDPR Recital 47 legitimate-interest opt-out path.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "trust_network_consent_20260509"
down_revision: str | None = "recurring_billing_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shopify_app_settings",
        sa.Column(
            "trust_network_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column(
        "shopify_app_settings",
        "trust_network_enabled",
        schema="public",
    )
