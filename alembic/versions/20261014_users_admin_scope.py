"""Staff roles: which admin sections a platform admin may open.

Revision ID: users_admin_scope_20261014
Revises: bis_templates_20261009
Create Date: 2026-10-14

NULL (every existing admin) or 'all' is a full super admin. 'support',
'finance' and 'ops' are limited by the allow-list in
``src/api/dependencies/auth.py``; any other value is denied everything.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "users_admin_scope_20261014"
down_revision: str | Sequence[str] | None = "bis_templates_20261009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("admin_scope", sa.String(length=20), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("users", "admin_scope", schema="public")
