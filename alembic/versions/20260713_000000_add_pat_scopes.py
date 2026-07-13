"""Add per-token scopes to personal_access_tokens.

NULL scopes = unrestricted legacy token (pre-scopes behaviour preserved).
A JSONB array of scope strings ("catalog:read", "orders:write", …, or "*")
otherwise; enforced centrally in the PAT auth dependency.

Revision ID: pat_scopes_20260713
Revises: whatsapp_access_20260705
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "pat_scopes_20260713"
down_revision: str | None = "whatsapp_access_20260705"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "personal_access_tokens",
        sa.Column("scopes", JSONB(), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("personal_access_tokens", "scopes", schema="public")
