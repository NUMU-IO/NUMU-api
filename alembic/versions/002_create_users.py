"""create users table in public schema

Revision ID: 002_create_users
Revises: 001_create_tenants
Create Date: 2026-01-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '002_create_users'
down_revision: Union[str, None] = '001_create_tenants'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create users table in public schema."""
    # Ensure we're in public schema
    op.execute("SET search_path TO public")
    
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('avatar_url', sa.String(length=500), nullable=True),
        sa.Column('role', sa.Enum('SUPER_ADMIN', 'ADMIN', 'STORE_OWNER', 'STORE_MANAGER', 'CUSTOMER', name='userrole'), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', 'PENDING_VERIFICATION', 'SUSPENDED', name='userstatus'), nullable=False),
        sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        schema='public'
    )
    
    # Create indexes
    op.create_index(
        'ix_public_users_email',
        'users',
        ['email'],
        unique=True,
        schema='public'
    )


def downgrade() -> None:
    """Drop users table."""
    op.execute("SET search_path TO public")
    
    op.drop_index('ix_public_users_email', table_name='users', schema='public')
    op.drop_table('users', schema='public')
    
    # Drop enums
    op.execute("DROP TYPE IF EXISTS userrole")
    op.execute("DROP TYPE IF EXISTS userstatus")
