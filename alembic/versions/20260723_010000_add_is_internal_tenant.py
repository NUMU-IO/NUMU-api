"""Add is_internal flag to tenants for excluding test/demo stores from analytics.

Revision ID: is_internal_20260723
Revises: marketing_campaigns_20260722
Create Date: 2026-07-23

Adds a boolean ``is_internal`` column (default false) so platform admins
can mark test/sandbox tenants that were created through normal signup
(not the demo flow) and exclude them from all admin dashboard aggregates
without affecting their lifecycle state.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "is_internal_20260723"
down_revision: str = "marketing_campaigns_20260722"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "is_internal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="public",
    )
    op.create_index(
        "ix_tenants_is_internal",
        "tenants",
        ["is_internal"],
        schema="public",
        postgresql_where=sa.text("is_internal = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_is_internal", table_name="tenants", schema="public")
    op.drop_column("tenants", "is_internal", schema="public")
