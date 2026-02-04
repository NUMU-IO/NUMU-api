"""add coupon table and order coupon fields

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-02-02 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: str | None = 'a3b4c5d6e7f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Create discounttype enum (idempotent)
    conn.exec_driver_sql("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'discounttype') THEN
                CREATE TYPE public.discounttype AS ENUM ('percentage', 'fixed_amount');
            END IF;
        END $$;
    """)

    # 2. Create coupons table via raw SQL (avoids SQLAlchemy enum auto-creation bug)
    table_exists = conn.exec_driver_sql(
        "SELECT to_regclass('public.coupons')"
    ).scalar()

    if table_exists is None:
        conn.exec_driver_sql("""
            CREATE TABLE public.coupons (
                id UUID NOT NULL PRIMARY KEY,
                tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
                store_id UUID NOT NULL REFERENCES public.stores(id) ON DELETE CASCADE,
                code VARCHAR(50) NOT NULL,
                description TEXT,
                discount_type public.discounttype NOT NULL,
                discount_value INTEGER NOT NULL,
                min_order_amount INTEGER NOT NULL DEFAULT 0,
                max_discount_amount INTEGER,
                max_uses INTEGER,
                max_uses_per_customer INTEGER,
                current_usage_count INTEGER NOT NULL DEFAULT 0,
                valid_from TIMESTAMPTZ,
                valid_to TIMESTAMPTZ,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                extra_data JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_coupons_store_code UNIQUE (store_id, code)
            )
        """)

    # 3. Create indexes (IF NOT EXISTS for idempotency)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_public_coupons_store_id ON public.coupons (store_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_public_coupons_code ON public.coupons (code)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_public_coupons_tenant_id ON public.coupons (tenant_id)"
    )

    # 4. Add coupon columns to orders table (skip if already exist)
    col_exists = conn.exec_driver_sql("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'orders' AND column_name = 'coupon_code'
    """).scalar()

    if not col_exists:
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
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_public_orders_coupon_id ON public.orders (coupon_id)"
        )


def downgrade() -> None:
    conn = op.get_bind()

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
    conn.exec_driver_sql("DROP TYPE IF EXISTS public.discounttype")
