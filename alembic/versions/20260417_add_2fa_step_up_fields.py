"""Add step-up and policy fields to two_factor_auth.

Revision ID: 2fa_stepup_001
Revises: add_perms_sys_001
Create Date: 2026-04-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2fa_stepup_001"
down_revision: str | None = "add_perms_sys_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "two_factor_auth",
        sa.Column(
            "enforced_by_policy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="public",
    )
    op.add_column(
        "two_factor_auth",
        sa.Column(
            "last_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "two_factor_auth",
        sa.Column(
            "backup_codes_hash",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("two_factor_auth", "backup_codes_hash", schema="public")
    op.drop_column("two_factor_auth", "last_verified_at", schema="public")
    op.drop_column("two_factor_auth", "enforced_by_policy", schema="public")
