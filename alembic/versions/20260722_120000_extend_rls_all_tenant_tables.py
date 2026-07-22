"""Extend RLS policies to EVERY tenant_id table (was only 8 of ~72).

The 2026-02-03 migration added Row-Level Security to 8 tables (stores,
products, orders, customers, categories, invoices, customer_addresses,
coupons). The other ~60 tenant-scoped tables — including pages, articles,
metafield_values, blogs, product_variants, inventory_levels, locations,
gift_cards, promotions, shipments, refunds, and the rest — had NO RLS at all,
so even a working RLS backstop wouldn't have protected them.

This discovers every public table with a `tenant_id` column and applies the
SAME policy set the original migration used (tenant-isolation select/insert/
update/delete keyed on `app.current_tenant`, plus the `admin_bypass` escape
for `app.rls_bypass` sessions). Idempotent: drops each policy before creating
it, so re-running or overlapping the original 8 is safe.

⚠️ RLS only ENFORCES when the app connects as a NON-SUPERUSER role — a
superuser bypasses every policy, and `FORCE ROW LEVEL SECURITY` does not change
that. This migration makes the policies EXIST and correct; enabling enforcement
is the separate, gated role switch documented in docs/REports/RLS-enforcement.md.
Under the current superuser connection this migration is observably inert.

Revision ID: rls_all_tenant_20260722
Revises: blogs_articles_20260721
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "rls_all_tenant_20260722"
down_revision: str | None = "blogs_articles_20260721"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Tables that carry tenant_id but must NOT be RLS-restricted this way:
#   - tenants: the tenant registry itself (its PK is `id`, no tenant_id — never
#     matched anyway, listed for clarity).
# Marketplace tables (purchases, reviews) are user-scoped, not tenant-scoped,
# and have their own policies from 20260507_marketplace_rls — they have no
# tenant_id column so they are not matched here.
_EXCLUDE = {"tenants"}

_POLICIES = (
    "tenant_isolation_select",
    "tenant_isolation_insert",
    "tenant_isolation_update",
    "tenant_isolation_delete",
    "admin_bypass",
)


def _tenant_tables(conn) -> list[str]:
    rows = conn.exec_driver_sql(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND a.attname = 'tenant_id'
          AND NOT a.attisdropped
        ORDER BY c.relname
        """
    ).fetchall()
    return [r[0] for r in rows]


def upgrade() -> None:
    conn = op.get_bind()

    # Ensure the helper functions exist (created by the 2026-02-03 migration;
    # re-create defensively so this migration stands alone).
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.get_current_tenant_id()
        RETURNS uuid AS $$
        BEGIN
            RETURN NULLIF(current_setting('app.current_tenant', true), '')::uuid;
        EXCEPTION WHEN OTHERS THEN RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE SECURITY DEFINER;
    """)
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.is_rls_bypassed()
        RETURNS boolean AS $$
        BEGIN
            RETURN COALESCE(NULLIF(current_setting('app.rls_bypass', true), '')::boolean, false);
        EXCEPTION WHEN OTHERS THEN RETURN false;
        END;
        $$ LANGUAGE plpgsql STABLE SECURITY DEFINER;
    """)

    for table in _tenant_tables(conn):
        if table in _EXCLUDE:
            continue
        conn.exec_driver_sql(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;")
        conn.exec_driver_sql(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;")
        for pol in _POLICIES:
            conn.exec_driver_sql(f"DROP POLICY IF EXISTS {pol} ON public.{table};")
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_select ON public.{table}
                FOR SELECT USING (tenant_id = public.get_current_tenant_id());
        """)
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_insert ON public.{table}
                FOR INSERT WITH CHECK (tenant_id = public.get_current_tenant_id());
        """)
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_update ON public.{table}
                FOR UPDATE USING (tenant_id = public.get_current_tenant_id())
                WITH CHECK (tenant_id = public.get_current_tenant_id());
        """)
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_delete ON public.{table}
                FOR DELETE USING (tenant_id = public.get_current_tenant_id());
        """)
        conn.exec_driver_sql(f"""
            CREATE POLICY admin_bypass ON public.{table}
                FOR ALL USING (public.is_rls_bypassed() = true)
                WITH CHECK (public.is_rls_bypassed() = true);
        """)


def downgrade() -> None:
    conn = op.get_bind()
    for table in _tenant_tables(conn):
        if table in _EXCLUDE:
            continue
        for pol in _POLICIES:
            conn.exec_driver_sql(f"DROP POLICY IF EXISTS {pol} ON public.{table};")
        conn.exec_driver_sql(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;")
