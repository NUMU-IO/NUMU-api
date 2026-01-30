"""add cart and cart_items tables

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-01-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create carts table
    op.create_table(
        'carts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('store_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('public.stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('public.customers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('public.tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema='public',
    )
    op.create_index('ix_public_carts_store_id', 'carts', ['store_id'], schema='public')
    op.create_index('ix_public_carts_customer_id', 'carts', ['customer_id'], schema='public')
    op.create_index('ix_public_carts_tenant_id', 'carts', ['tenant_id'], schema='public')
    # Unique constraint: one active cart per customer per store
    op.create_index(
        'ix_public_carts_store_customer_unique',
        'carts',
        ['store_id', 'customer_id'],
        unique=True,
        schema='public',
    )

    # Create cart_items table
    op.create_table(
        'cart_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('cart_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('public.carts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('public.products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('variant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('public.tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema='public',
    )
    op.create_index('ix_public_cart_items_cart_id', 'cart_items', ['cart_id'], schema='public')
    op.create_index('ix_public_cart_items_product_id', 'cart_items', ['product_id'], schema='public')
    op.create_index('ix_public_cart_items_tenant_id', 'cart_items', ['tenant_id'], schema='public')
    # Unique constraint: one entry per product+variant per cart.
    # Split into two partial indexes because PostgreSQL treats NULL != NULL,
    # so a single unique index on (cart_id, product_id, variant_id) would
    # allow duplicate rows when variant_id IS NULL.
    op.execute(
        'CREATE UNIQUE INDEX ix_public_cart_items_cart_product_variant_unique '
        'ON public.cart_items (cart_id, product_id, variant_id) '
        'WHERE variant_id IS NOT NULL'
    )
    op.execute(
        'CREATE UNIQUE INDEX ix_public_cart_items_cart_product_no_variant_unique '
        'ON public.cart_items (cart_id, product_id) '
        'WHERE variant_id IS NULL'
    )


def downgrade() -> None:
    # Drop cart_items
    op.execute('DROP INDEX IF EXISTS public.ix_public_cart_items_cart_product_no_variant_unique')
    op.execute('DROP INDEX IF EXISTS public.ix_public_cart_items_cart_product_variant_unique')
    op.drop_index('ix_public_cart_items_tenant_id', table_name='cart_items', schema='public')
    op.drop_index('ix_public_cart_items_product_id', table_name='cart_items', schema='public')
    op.drop_index('ix_public_cart_items_cart_id', table_name='cart_items', schema='public')
    op.drop_table('cart_items', schema='public')

    # Drop carts
    op.drop_index('ix_public_carts_store_customer_unique', table_name='carts', schema='public')
    op.drop_index('ix_public_carts_tenant_id', table_name='carts', schema='public')
    op.drop_index('ix_public_carts_customer_id', table_name='carts', schema='public')
    op.drop_index('ix_public_carts_store_id', table_name='carts', schema='public')
    op.drop_table('carts', schema='public')
