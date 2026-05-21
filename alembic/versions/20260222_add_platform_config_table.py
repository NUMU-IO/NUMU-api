"""Add platform_config table for landing page settings.

Revision ID: a1c2e3f4d5b6
Revises: f7e6d5c4b3a2
Create Date: 2026-02-22

Adds:
- public.platform_config — platform-wide configuration as JSONB key-value pairs
- Seeds default landing page config with all sections visible
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1c2e3f4d5b6"
down_revision: str | None = "f7e6d5c4b3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
        schema="public",
    )
    op.create_index(
        "ix_platform_config_key",
        "platform_config",
        ["key"],
        schema="public",
    )

    # Seed default landing page config
    op.execute(
        """
        INSERT INTO public.platform_config (key, value, description)
        VALUES (
            'landing_page',
            '{"sections": {"hero": {"visible": true, "order": 0}, "preview": {"visible": true, "order": 1}, "features": {"visible": true, "order": 2}, "import-showcase": {"visible": true, "order": 3}, "ai-showcase": {"visible": true, "order": 4}, "multichannel-showcase": {"visible": true, "order": 5}, "integrations": {"visible": true, "order": 6}, "testimonials": {"visible": true, "order": 7}, "cta": {"visible": true, "order": 8}, "footer": {"visible": true, "order": 9}}}',
            'Landing page section visibility and ordering'
        )
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_config_key", table_name="platform_config", schema="public"
    )
    op.drop_table("platform_config", schema="public")
