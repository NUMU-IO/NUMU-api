"""Add PDF storage fields to invoices table.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-06

Adds pdf_r2_key and pdf_url columns for storing generated invoice
PDFs in Cloudflare R2 and caching their public URLs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("pdf_r2_key", sa.String(500), nullable=True),
        schema="public",
    )
    op.add_column(
        "invoices",
        sa.Column("pdf_url", sa.String(500), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("invoices", "pdf_url", schema="public")
    op.drop_column("invoices", "pdf_r2_key", schema="public")
