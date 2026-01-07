"""create tenants table in public schema

Revision ID: 001_create_tenants
Revises: 
Create Date: 2026-01-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_create_tenants'
down_revision: Union[str, None] = None  # Update this if you have existing migrations
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create tenants table in public schema."""
    # Ensure we're in public schema
    op.execute("SET search_path TO public")
    
    op.create_table(
        'tenants',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('subdomain', sa.String(length=63), nullable=False),
        sa.Column('schema_name', sa.String(length=100), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('plan', sa.String(length=50), server_default='free', nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('settings', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        schema='public'
    )
    
    # Create indexes
    op.create_index(
        'ix_public_tenants_subdomain',
        'tenants',
        ['subdomain'],
        unique=True,
        schema='public'
    )
    
    op.create_index(
        'ix_public_tenants_schema_name',
        'tenants',
        ['schema_name'],
        unique=True,
        schema='public'
    )
    
    op.create_index(
        'ix_public_tenants_is_active',
        'tenants',
        ['is_active'],
        schema='public'
    )


def downgrade() -> None:
    """Drop tenants table."""
    op.execute("SET search_path TO public")
    
    op.drop_index('ix_public_tenants_is_active', table_name='tenants', schema='public')
    op.drop_index('ix_public_tenants_schema_name', table_name='tenants', schema='public')
    op.drop_index('ix_public_tenants_subdomain', table_name='tenants', schema='public')
    op.drop_table('tenants', schema='public')
