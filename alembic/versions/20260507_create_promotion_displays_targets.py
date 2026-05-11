"""Create promotion_displays and promotion_targets tables.

`promotion_displays` captures *when and how* a promotion is shown
(triggers, frequency, page/device targeting). `promotion_targets`
captures *who* the promotion applies to (audience, product/category,
customer tag, geo).

Both are one-to-many from `promotions` with ON DELETE CASCADE so
removing a promotion wipes all its display/target rows.

RLS follows the four-policy + admin_bypass pattern.

Revision ID: promo_displays_20260507
Revises: promotions_20260507
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "promo_displays_20260507"
down_revision: str | None = "promotions_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES = ["promotion_displays", "promotion_targets"]


def _apply_rls(table: str) -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_select ON public.{table}
            FOR SELECT
            USING (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_insert ON public.{table}
            FOR INSERT
            WITH CHECK (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_update ON public.{table}
            FOR UPDATE
            USING (tenant_id = public.get_current_tenant_id())
            WITH CHECK (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_delete ON public.{table}
            FOR DELETE
            USING (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY admin_bypass ON public.{table}
            FOR ALL
            USING (public.is_rls_bypassed() = true)
            WITH CHECK (public.is_rls_bypassed() = true);
    """)


def _drop_rls(table: str) -> None:
    conn = op.get_bind()
    for policy in (
        "tenant_isolation_select",
        "tenant_isolation_insert",
        "tenant_isolation_update",
        "tenant_isolation_delete",
        "admin_bypass",
    ):
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS {policy} ON public.{table};")
    conn.exec_driver_sql(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;")


def upgrade() -> None:
    trigger_enum = postgresql.ENUM(
        name="display_trigger_enum",
        schema="public",
        create_type=False,
    )
    frequency_enum = postgresql.ENUM(
        name="display_frequency_enum",
        schema="public",
        create_type=False,
    )
    target_kind_enum = postgresql.ENUM(
        name="target_kind_enum",
        schema="public",
        create_type=False,
    )

    op.create_table(
        "promotion_displays",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.promotions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", trigger_enum, nullable=False),
        sa.Column(
            "trigger_value",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("frequency", frequency_enum, nullable=False),
        sa.Column(
            "pages",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "device_targets",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text('\'["desktop","mobile"]\'::jsonb'),
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )

    op.create_index(
        "ix_promotion_displays_tenant_id",
        "promotion_displays",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_displays_promotion_id",
        "promotion_displays",
        ["promotion_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_displays_promo_enabled",
        "promotion_displays",
        ["promotion_id", "is_enabled"],
        schema="public",
    )

    op.create_table(
        "promotion_targets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.promotions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_kind", target_kind_enum, nullable=False),
        sa.Column(
            "target_value",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "inclusion",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )

    op.create_index(
        "ix_promotion_targets_tenant_id",
        "promotion_targets",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_targets_promotion_id",
        "promotion_targets",
        ["promotion_id"],
        schema="public",
    )

    for table in _RLS_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _RLS_TABLES:
        _drop_rls(table)

    for idx in (
        "ix_promotion_targets_promotion_id",
        "ix_promotion_targets_tenant_id",
    ):
        op.drop_index(idx, table_name="promotion_targets", schema="public")
    op.drop_table("promotion_targets", schema="public")

    for idx in (
        "ix_promotion_displays_promo_enabled",
        "ix_promotion_displays_promotion_id",
        "ix_promotion_displays_tenant_id",
    ):
        op.drop_index(idx, table_name="promotion_displays", schema="public")
    op.drop_table("promotion_displays", schema="public")
