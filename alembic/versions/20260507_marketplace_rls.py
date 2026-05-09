"""Add RLS policies for marketplace user-scoped tables.

Revision ID: marketplace_rls_20260507
Revises: marketplace_reviews_20260507
Create Date: 2026-05-07

Defense-in-depth for the marketplace tables that hold user-private
data:

  * `marketplace_theme_purchases` — financial records keyed on
    `user_id` (the buyer). A SELECT bug shouldn't leak someone else's
    purchase history.
  * `marketplace_theme_reviews` — UPDATE/DELETE keyed on `user_id`
    (the author). SELECT stays public because reviews are inherently
    public; it's the write paths we don't want one user pwning
    another on. (We still expose a SELECT policy so the table's
    behavior is symmetric — `USING (true)` on SELECT just means "no
    extra filter".)

Pattern matches the existing tenant-RLS migration
(20260203_add_rls_policies.py):

  * `app.current_user` session variable carries the request's user.
  * `is_rls_bypassed()` (already created by the earlier RLS migration)
    is reused for admin / cross-user reads.
  * Policies are written so a session with neither `app.current_user`
    set nor bypass=true gets zero rows — fail closed.

Wiring into the request lifecycle is intentionally NOT part of this
migration. `get_db_session` / `get_admin_db_session` already set
bypass=true for admin sessions; tightening regular merchant sessions
to set `app.current_user` is a follow-up that lives in connection.py
when the team is ready to engage strict mode. Until then the app's
existing session lifecycle (tenant context only) will trip RLS unless
bypass is on — see the partner connection.py change in the same PR.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "marketplace_rls_20260507"
down_revision: str | None = "marketplace_reviews_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_SCOPED_TABLES = (
    "marketplace_theme_purchases",
    "marketplace_theme_reviews",
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── Helper: get_current_user_id() ─────────────────────────────────────
    # Mirrors the existing get_current_tenant_id(): returns NULL on
    # missing / malformed value so policies fail closed.
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.get_current_user_id()
        RETURNS uuid AS $$
        BEGIN
            RETURN NULLIF(current_setting('app.current_user', true), '')::uuid;
        EXCEPTION
            WHEN invalid_text_representation THEN
                RETURN NULL;
            WHEN OTHERS THEN
                RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE SECURITY DEFINER;
    """)

    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.set_user_context(p_user_id uuid)
        RETURNS void AS $$
        BEGIN
            PERFORM set_config('app.current_user', p_user_id::text, true);
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER;
    """)

    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION public.clear_user_context()
        RETURNS void AS $$
        BEGIN
            PERFORM set_config('app.current_user', '', true);
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER;
    """)

    # ── Purchases: full user-scoped RLS ───────────────────────────────────
    # Buyers can read/insert/update/delete only their own rows. Admin
    # ops (refund issuance via /marketplace/admin/...) flip rls_bypass
    # in the admin session before touching the table.
    #
    # NOTE: each `exec_driver_sql` must be a SINGLE statement —
    # asyncpg's `prepare()` rejects multi-statement strings with
    # "cannot insert multiple commands into a prepared statement". We
    # ran into this on the first deploy and split every ALTER pair.
    conn.exec_driver_sql(
        "ALTER TABLE public.marketplace_theme_purchases ENABLE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql(
        "ALTER TABLE public.marketplace_theme_purchases FORCE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql("""
        CREATE POLICY purchases_self_select ON public.marketplace_theme_purchases
            FOR SELECT
            USING (user_id = public.get_current_user_id());
    """)
    conn.exec_driver_sql("""
        CREATE POLICY purchases_self_insert ON public.marketplace_theme_purchases
            FOR INSERT
            WITH CHECK (user_id = public.get_current_user_id());
    """)
    conn.exec_driver_sql("""
        CREATE POLICY purchases_self_update ON public.marketplace_theme_purchases
            FOR UPDATE
            USING (user_id = public.get_current_user_id())
            WITH CHECK (user_id = public.get_current_user_id());
    """)
    conn.exec_driver_sql("""
        CREATE POLICY purchases_self_delete ON public.marketplace_theme_purchases
            FOR DELETE
            USING (user_id = public.get_current_user_id());
    """)
    conn.exec_driver_sql("""
        CREATE POLICY purchases_admin_bypass ON public.marketplace_theme_purchases
            FOR ALL
            USING (public.is_rls_bypassed() = true)
            WITH CHECK (public.is_rls_bypassed() = true);
    """)

    # ── Reviews: public SELECT, owner-only writes ────────────────────────
    # Anyone browsing a theme should be able to read its reviews — the
    # USING (true) on SELECT keeps the catalog reads working without
    # requiring the visitor to have a session-scoped user_id. Writes
    # stay strictly owner-bound.
    conn.exec_driver_sql(
        "ALTER TABLE public.marketplace_theme_reviews ENABLE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql(
        "ALTER TABLE public.marketplace_theme_reviews FORCE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql("""
        CREATE POLICY reviews_public_select ON public.marketplace_theme_reviews
            FOR SELECT
            USING (true);
    """)
    conn.exec_driver_sql("""
        CREATE POLICY reviews_owner_insert ON public.marketplace_theme_reviews
            FOR INSERT
            WITH CHECK (user_id = public.get_current_user_id());
    """)
    conn.exec_driver_sql("""
        CREATE POLICY reviews_owner_update ON public.marketplace_theme_reviews
            FOR UPDATE
            USING (user_id = public.get_current_user_id())
            WITH CHECK (user_id = public.get_current_user_id());
    """)
    conn.exec_driver_sql("""
        CREATE POLICY reviews_owner_delete ON public.marketplace_theme_reviews
            FOR DELETE
            USING (user_id = public.get_current_user_id());
    """)
    conn.exec_driver_sql("""
        CREATE POLICY reviews_admin_bypass ON public.marketplace_theme_reviews
            FOR ALL
            USING (public.is_rls_bypassed() = true)
            WITH CHECK (public.is_rls_bypassed() = true);
    """)


def downgrade() -> None:
    conn = op.get_bind()

    for table in USER_SCOPED_TABLES:
        # Drop every policy we added — names are stable so we can list
        # them explicitly rather than enumerating from pg_policies.
        for policy in (
            "purchases_self_select",
            "purchases_self_insert",
            "purchases_self_update",
            "purchases_self_delete",
            "purchases_admin_bypass",
            "reviews_public_select",
            "reviews_owner_insert",
            "reviews_owner_update",
            "reviews_owner_delete",
            "reviews_admin_bypass",
        ):
            conn.exec_driver_sql(f"DROP POLICY IF EXISTS {policy} ON public.{table};")
        conn.exec_driver_sql(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;")

    conn.exec_driver_sql("DROP FUNCTION IF EXISTS public.clear_user_context();")
    conn.exec_driver_sql("DROP FUNCTION IF EXISTS public.set_user_context(uuid);")
    conn.exec_driver_sql("DROP FUNCTION IF EXISTS public.get_current_user_id();")
