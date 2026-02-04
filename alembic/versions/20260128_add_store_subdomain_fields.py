"""add store subdomain and theme fields

Revision ID: a3b4c5d6e7f8
Revises: 2d2b2176a338
Create Date: 2026-01-28 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: str | None = 'add_invoices_table'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add subdomain column (unique, indexed for fast lookups)
    op.add_column('stores',
        sa.Column('subdomain', sa.String(63), nullable=True),
        schema='public'
    )
    op.create_index(
        'ix_public_stores_subdomain',
        'stores',
        ['subdomain'],
        unique=True,
        schema='public'
    )

    # Add custom_domain column (unique, indexed for custom domain lookups)
    op.add_column('stores',
        sa.Column('custom_domain', sa.String(255), nullable=True),
        schema='public'
    )
    op.create_index(
        'ix_public_stores_custom_domain',
        'stores',
        ['custom_domain'],
        unique=True,
        schema='public'
    )

    # Add theme_settings JSONB column for NUMU-shop customization
    op.add_column('stores',
        sa.Column('theme_settings', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='{}'),
        schema='public'
    )


def downgrade() -> None:
    # Remove theme_settings column
    op.drop_column('stores', 'theme_settings', schema='public')

    # Remove custom_domain index and column
    op.drop_index('ix_public_stores_custom_domain', table_name='stores', schema='public')
    op.drop_column('stores', 'custom_domain', schema='public')

    # Remove subdomain index and column
    op.drop_index('ix_public_stores_subdomain', table_name='stores', schema='public')
    op.drop_column('stores', 'subdomain', schema='public')
