"""add coupon table and order coupon fields

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-02-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create discounttype enum
    discounttype_enum = sa.Enum(
        'percentage', 'fixed_amount',
        name='discounttype',
        schema='public',
    )
    discounttype_enum.create(op.get_bind(), checkfirst=True)

    # 2. Create coupons table
    op.create_table(
        'coupons',
        sa.Column('store_id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column(
            'discount_type',
            discounttype_enum,
            nullable=False,
        ),
        sa.Column('discount_value', sa.Integer(), nullable=False),
        sa.Column('min_order_amount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_discount_amount', sa.Integer(), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('max_uses_per_customer', sa.Integer(), nullable=True),
        sa.Column('current_usage_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('extra_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Base mixin columns
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        # Constraints
        sa.ForeignKeyConstraint(['store_id'], ['public.stores.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['public.tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'code', name='uq_coupons_store_code'),
        schema='public',
    )

    # 3. Create indexes for coupons
    op.create_index(
        op.f('ix_public_coupons_store_id'),
        'coupons', ['store_id'],
        unique=False, schema='public',
    )
    op.create_index(
        op.f('ix_public_coupons_code'),
        'coupons', ['code'],
        unique=False, schema='public',
    )
    op.create_index(
        op.f('ix_public_coupons_tenant_id'),
        'coupons', ['tenant_id'],
        unique=False, schema='public',
    )

    # 4. Add coupon columns to orders table
    op.add_column(
        'orders',
        sa.Column('coupon_code', sa.String(length=50), nullable=True),
        schema='public',
    )
    op.add_column(
        'orders',
        sa.Column('coupon_id', sa.UUID(), nullable=True),
        schema='public',
    )
    op.create_foreign_key(
        'fk_orders_coupon_id_coupons',
        'orders', 'coupons',
        ['coupon_id'], ['id'],
        source_schema='public',
        referent_schema='public',
        ondelete='SET NULL',
    )
    op.create_index(
        op.f('ix_public_orders_coupon_id'),
        'orders', ['coupon_id'],
        unique=False, schema='public',
    )


def downgrade() -> None:
    # 1. Remove coupon columns from orders
    op.drop_index(op.f('ix_public_orders_coupon_id'), table_name='orders', schema='public')
    op.drop_constraint('fk_orders_coupon_id_coupons', 'orders', schema='public', type_='foreignkey')
    op.drop_column('orders', 'coupon_id', schema='public')
    op.drop_column('orders', 'coupon_code', schema='public')

    # 2. Drop coupons table
    op.drop_index(op.f('ix_public_coupons_tenant_id'), table_name='coupons', schema='public')
    op.drop_index(op.f('ix_public_coupons_code'), table_name='coupons', schema='public')
    op.drop_index(op.f('ix_public_coupons_store_id'), table_name='coupons', schema='public')
    op.drop_table('coupons', schema='public')

    # 3. Drop discounttype enum
    sa.Enum(name='discounttype', schema='public').drop(op.get_bind(), checkfirst=True)
