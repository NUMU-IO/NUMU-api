"""Fix coupons table to match CouponModel.

Revision ID: c3d4e5f6a7b8_fix
Revises: b2c3d4e5f6a7
Create Date: 2026-02-04

The original coupon migration created columns that don't match the model.
This migration drops and recreates the coupons table with the correct schema.
"""

from alembic import op

# revision identifiers
revision = "c3d4e5f6a7b8f"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Drop order FK to coupons first
    conn.exec_driver_sql("""
        ALTER TABLE public.orders DROP CONSTRAINT IF EXISTS fk_orders_coupon_id_coupons
    """)
    conn.exec_driver_sql("DROP INDEX IF EXISTS public.ix_public_orders_coupon_id")

    # 2. Drop the old coupons table and its indexes
    conn.exec_driver_sql("DROP TABLE IF EXISTS public.coupons CASCADE")

    # 3. Drop old enum, create correct one
    conn.exec_driver_sql("DROP TYPE IF EXISTS public.discounttype")
    conn.exec_driver_sql("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'coupontype') THEN
                CREATE TYPE public.coupontype AS ENUM ('percentage', 'fixed', 'free_shipping');
            END IF;
        END $$;
    """)

    # 4. Create coupons table matching the CouponModel exactly
    conn.exec_driver_sql("""
        CREATE TABLE public.coupons (
            id UUID NOT NULL PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            store_id UUID NOT NULL REFERENCES public.stores(id) ON DELETE CASCADE,
            code VARCHAR(50) NOT NULL,
            coupon_type public.coupontype NOT NULL,
            value NUMERIC(12, 2) NOT NULL DEFAULT 0,
            min_order_amount NUMERIC(12, 2),
            max_discount_amount NUMERIC(12, 2),
            usage_limit INTEGER,
            usage_count INTEGER NOT NULL DEFAULT 0,
            valid_from TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_coupons_store_code UNIQUE (store_id, code)
        )
    """)

    # 5. Create indexes
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_public_coupons_store_id ON public.coupons (store_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_public_coupons_code ON public.coupons (code)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_public_coupons_tenant_id ON public.coupons (tenant_id)"
    )

    # 6. Re-add order FK (coupon_id column already exists from previous migration)
    col_exists = conn.exec_driver_sql("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'orders' AND column_name = 'coupon_id'
    """).scalar()
    if col_exists:
        conn.exec_driver_sql("""
            ALTER TABLE public.orders
            ADD CONSTRAINT fk_orders_coupon_id_coupons
            FOREIGN KEY (coupon_id) REFERENCES public.coupons(id) ON DELETE SET NULL
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_public_orders_coupon_id ON public.orders (coupon_id)"
        )

    # 7. Re-apply RLS policies for coupons (they were dropped with CASCADE)
    conn.exec_driver_sql("ALTER TABLE public.coupons ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE public.coupons FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_select ON public.coupons
            FOR SELECT USING (tenant_id = public.get_current_tenant_id())
    """)
    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_insert ON public.coupons
            FOR INSERT WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)
    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_update ON public.coupons
            FOR UPDATE
            USING (tenant_id = public.get_current_tenant_id())
            WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)
    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_delete ON public.coupons
            FOR DELETE USING (tenant_id = public.get_current_tenant_id())
    """)
    conn.exec_driver_sql("""
        CREATE POLICY admin_bypass ON public.coupons
            FOR ALL
            USING (public.is_rls_bypassed() = true)
            WITH CHECK (public.is_rls_bypassed() = true)
    """)


def downgrade() -> None:
    # This migration is a fix — downgrade would revert to the broken schema
    # which is not useful, so we just pass
    pass
