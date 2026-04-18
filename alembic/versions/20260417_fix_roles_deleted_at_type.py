"""Fix roles.deleted_at column type (boolean -> timestamptz).

The add_permissions_system migration created public.roles.deleted_at as
boolean NOT NULL DEFAULT false, which is inconsistent with every other
deleted_at column in the codebase (timestamptz NULL) and makes the
`deleted_at IS NULL` soft-delete filter used by RoleRepository match
zero rows. Any cloned tenant role was therefore invisible.

Revision ID: roles_deleted_at_fix_001
Revises: seed_perms_001
Create Date: 2026-04-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "roles_deleted_at_fix_001"
down_revision: str | None = "seed_perms_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.roles
            ALTER COLUMN deleted_at DROP DEFAULT,
            ALTER COLUMN deleted_at DROP NOT NULL,
            ALTER COLUMN deleted_at TYPE timestamp with time zone
                USING CASE WHEN deleted_at THEN now() ELSE NULL END
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.roles
            ALTER COLUMN deleted_at TYPE boolean
                USING (deleted_at IS NOT NULL),
            ALTER COLUMN deleted_at SET NOT NULL,
            ALTER COLUMN deleted_at SET DEFAULT false
        """
    )
