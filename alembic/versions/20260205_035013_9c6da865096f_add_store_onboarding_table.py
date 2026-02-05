"""add store_onboarding table

Revision ID: 9c6da865096f
Revises: f6a7b8c9d0e1
Create Date: 2026-02-05 03:50:13.529847

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9c6da865096f'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('audit_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('customer_id', sa.UUID(), nullable=True),
    sa.Column('store_id', sa.UUID(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('resource_type', sa.String(length=50), nullable=True),
    sa.Column('resource_id', sa.String(length=100), nullable=True),
    sa.Column('action', sa.String(length=50), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    schema='public'
    )
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'], unique=False, schema='public')
    op.create_index('ix_audit_logs_event_type', 'audit_logs', ['event_type'], unique=False, schema='public')
    op.create_index('ix_audit_logs_store_id', 'audit_logs', ['store_id'], unique=False, schema='public')
    op.create_index('ix_audit_logs_tenant_id', 'audit_logs', ['tenant_id'], unique=False, schema='public')
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'], unique=False, schema='public')

    op.create_table('store_onboarding',
    sa.Column('store_id', sa.UUID(), nullable=False),
    sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('is_completed', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_dismissed', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['public.stores.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema='public'
    )
    op.create_index('ix_store_onboarding_store_id', 'store_onboarding', ['store_id'], unique=True, schema='public')


def downgrade() -> None:
    op.drop_index('ix_store_onboarding_store_id', table_name='store_onboarding', schema='public')
    op.drop_table('store_onboarding', schema='public')
    op.drop_index('ix_audit_logs_user_id', table_name='audit_logs', schema='public')
    op.drop_index('ix_audit_logs_tenant_id', table_name='audit_logs', schema='public')
    op.drop_index('ix_audit_logs_store_id', table_name='audit_logs', schema='public')
    op.drop_index('ix_audit_logs_event_type', table_name='audit_logs', schema='public')
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs', schema='public')
    op.drop_table('audit_logs', schema='public')
