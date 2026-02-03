"""Add Row Level Security (RLS) policies for tenant isolation

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-02-03 12:00:00.000000

This migration adds PostgreSQL Row-Level Security policies to all tenant-scoped
tables to enforce data isolation at the database level. RLS provides an additional
security layer beyond application-level filtering.

Tables with RLS:
- stores
- products
- orders
- customers
- categories
- invoices
- customer_addresses
- coupons

The policies use a session variable `app.current_tenant` which must be set
before any query. This is handled by the application's database connection layer.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that require RLS policies (all have tenant_id column)
TENANT_SCOPED_TABLES = [
    'stores',
    'products',
    'orders',
    'customers',
    'categories',
    'invoices',
    'customer_addresses',
    'coupons',
]


def upgrade() -> None:
    """Enable RLS and create tenant isolation policies."""
    conn = op.get_bind()

    # Create the helper function for getting current tenant from session variable
    # This function is used in RLS policies to extract the tenant UUID
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.get_current_tenant_id()
        RETURNS uuid AS $$
        BEGIN
            -- Try to get the current tenant from session variable
            -- Returns NULL if not set, which will cause RLS to block access
            RETURN NULLIF(current_setting('app.current_tenant', true), '')::uuid;
        EXCEPTION
            WHEN invalid_text_representation THEN
                -- Handle case where value is not a valid UUID
                RETURN NULL;
            WHEN OTHERS THEN
                RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE SECURITY DEFINER;
    """)

    # Create the helper function to set tenant context
    # This is called at the beginning of each database session
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.set_tenant_context(p_tenant_id uuid)
        RETURNS void AS $$
        BEGIN
            PERFORM set_config('app.current_tenant', p_tenant_id::text, true);
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER;
    """)

    # Create the helper function to clear tenant context
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.clear_tenant_context()
        RETURNS void AS $$
        BEGIN
            PERFORM set_config('app.current_tenant', '', true);
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER;
    """)

    # Enable RLS and create policies for each tenant-scoped table
    for table_name in TENANT_SCOPED_TABLES:
        table_exists = conn.exec_driver_sql(
            f"SELECT to_regclass('public.{table_name}')"
        ).scalar()
        if table_exists is None:
            continue

        # Enable RLS on the table
        conn.exec_driver_sql(f"""
            ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY;
        """)

        # Force RLS for table owner as well (important for superusers)
        conn.exec_driver_sql(f"""
            ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY;
        """)

        # Create SELECT policy - allows reading rows where tenant_id matches
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_select ON public.{table_name}
                FOR SELECT
                USING (tenant_id = public.get_current_tenant_id());
        """)

        # Create INSERT policy - allows inserting rows with matching tenant_id
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_insert ON public.{table_name}
                FOR INSERT
                WITH CHECK (tenant_id = public.get_current_tenant_id());
        """)

        # Create UPDATE policy - allows updating only matching tenant's rows
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_update ON public.{table_name}
                FOR UPDATE
                USING (tenant_id = public.get_current_tenant_id())
                WITH CHECK (tenant_id = public.get_current_tenant_id());
        """)

        # Create DELETE policy - allows deleting only matching tenant's rows
        conn.exec_driver_sql(f"""
            CREATE POLICY tenant_isolation_delete ON public.{table_name}
                FOR DELETE
                USING (tenant_id = public.get_current_tenant_id());
        """)

    # Create a bypass policy for admin/service operations
    # This allows operations when app.current_tenant is set to a special bypass value
    # The bypass is controlled by setting app.rls_bypass = 'true'
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.is_rls_bypassed()
        RETURNS boolean AS $$
        BEGIN
            RETURN COALESCE(
                NULLIF(current_setting('app.rls_bypass', true), '')::boolean,
                false
            );
        EXCEPTION
            WHEN OTHERS THEN
                RETURN false;
        END;
        $$ LANGUAGE plpgsql STABLE SECURITY DEFINER;
    """)

    # Add bypass policies for admin operations
    for table_name in TENANT_SCOPED_TABLES:
        table_exists = conn.exec_driver_sql(
            f"SELECT to_regclass('public.{table_name}')"
        ).scalar()
        if table_exists is None:
            continue

        conn.exec_driver_sql(f"""
            CREATE POLICY admin_bypass ON public.{table_name}
                FOR ALL
                USING (public.is_rls_bypassed() = true)
                WITH CHECK (public.is_rls_bypassed() = true);
        """)


def downgrade() -> None:
    """Remove RLS policies and disable RLS."""
    conn = op.get_bind()

    # Remove policies and disable RLS for each table
    for table_name in TENANT_SCOPED_TABLES:
        table_exists = conn.exec_driver_sql(
            f"SELECT to_regclass('public.{table_name}')"
        ).scalar()
        if table_exists is None:
            continue

        # Drop all policies
        conn.exec_driver_sql(f"""
            DROP POLICY IF EXISTS tenant_isolation_select ON public.{table_name};
        """)
        conn.exec_driver_sql(f"""
            DROP POLICY IF EXISTS tenant_isolation_insert ON public.{table_name};
        """)
        conn.exec_driver_sql(f"""
            DROP POLICY IF EXISTS tenant_isolation_update ON public.{table_name};
        """)
        conn.exec_driver_sql(f"""
            DROP POLICY IF EXISTS tenant_isolation_delete ON public.{table_name};
        """)
        conn.exec_driver_sql(f"""
            DROP POLICY IF EXISTS admin_bypass ON public.{table_name};
        """)

        # Disable RLS on the table
        conn.exec_driver_sql(f"""
            ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY;
        """)

    # Drop helper functions
    conn.exec_driver_sql("""
        DROP FUNCTION IF EXISTS public.is_rls_bypassed();
    """)
    conn.exec_driver_sql("""
        DROP FUNCTION IF EXISTS public.clear_tenant_context();
    """)
    conn.exec_driver_sql("""
        DROP FUNCTION IF EXISTS public.set_tenant_context(uuid);
    """)
    conn.exec_driver_sql("""
        DROP FUNCTION IF EXISTS public.get_current_tenant_id();
    """)
