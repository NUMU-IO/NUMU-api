"""Add reason_en / reason_ar columns to product_bundles.

Lets merchants annotate *why* a bundle is recommended (e.g. "Customers
who bought this also needed a case", "Same-day shipping guaranteed when
ordered together"). Surfaced on the storefront FBT strip as a "Why this
bundle?" tooltip.

Both columns are optional — existing bundles keep rendering with no
tooltip. Nullable, short strings to match the existing
``section_title_*`` column convention.

Revision ID: bundle_reason_20260425
Revises: cod_deposit_fields_20260425
Create Date: 2026-04-25 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bundle_reason_20260425"
down_revision: str | None = "cod_deposit_fields_20260425"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_bundles",
        sa.Column("reason_en", sa.String(200), nullable=True),
        schema="public",
    )
    op.add_column(
        "product_bundles",
        sa.Column("reason_ar", sa.String(200), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("product_bundles", "reason_ar", schema="public")
    op.drop_column("product_bundles", "reason_en", schema="public")
