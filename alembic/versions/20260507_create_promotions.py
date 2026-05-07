"""Create promotions and promotion_translations tables.

Master table for the unified Offers/Promotions system. Every customer-facing
offer the merchant configures (discount code, automatic discount, popup,
banner, widget, cookie banner) is a row in `promotions`. The sidecar
`promotion_translations` table holds bilingual (en/ar) copy keyed by locale.

Surface-specific config lives polymorphically in the `content` JSONB column;
discount math for `automatic` surfaces lives in `discount_rule` JSONB. A
`coupon_id` FK points at the existing `coupons` row when the surface is
`discount_code`.

RLS follows the four-policy + admin_bypass pattern from
`20260203_add_rls_policies.py`.

Revision ID: promotions_20260507
Revises: promotion_enums_20260507
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "promotions_20260507"
down_revision: str | None = "promotion_enums_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES = ["promotions", "promotion_translations"]


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
    surface_enum = postgresql.ENUM(
        name="promotion_surface_enum",
        schema="public",
        create_type=False,
    )
    status_enum = postgresql.ENUM(
        name="promotion_status_enum",
        schema="public",
        create_type=False,
    )

    op.create_table(
        "promotions",
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
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("surface", surface_enum, nullable=False),
        sa.Column(
            "status",
            status_enum,
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "coupon_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.coupons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("discount_rule", postgresql.JSONB, nullable=True),
        sa.Column(
            "content",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "priority",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
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
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "surface != 'discount_code' OR coupon_id IS NOT NULL",
            name="ck_promotions_discount_code_has_coupon",
        ),
        sa.CheckConstraint(
            "surface = 'discount_code' OR coupon_id IS NULL",
            name="ck_promotions_non_code_has_no_coupon",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_promotions_window_valid",
        ),
        schema="public",
    )

    op.create_index(
        "ix_promotions_tenant_id",
        "promotions",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotions_store_id",
        "promotions",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotions_surface",
        "promotions",
        ["surface"],
        schema="public",
    )
    op.create_index(
        "ix_promotions_ends_at",
        "promotions",
        ["ends_at"],
        schema="public",
    )
    op.create_index(
        "ix_promotions_coupon_id",
        "promotions",
        ["coupon_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotions_tenant_store_status_surface",
        "promotions",
        ["tenant_id", "store_id", "status", "surface"],
        schema="public",
    )
    op.create_index(
        "ix_promotions_store_status_ends_at",
        "promotions",
        ["store_id", "status", "ends_at"],
        schema="public",
    )

    op.create_table(
        "promotion_translations",
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
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.UniqueConstraint(
            "promotion_id",
            "locale",
            name="uq_promotion_translations_promo_locale",
        ),
        schema="public",
    )

    op.create_index(
        "ix_promotion_translations_tenant_id",
        "promotion_translations",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_translations_promotion_id",
        "promotion_translations",
        ["promotion_id"],
        schema="public",
    )

    for table in _RLS_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _RLS_TABLES:
        _drop_rls(table)

    for idx in (
        "ix_promotion_translations_promotion_id",
        "ix_promotion_translations_tenant_id",
    ):
        op.drop_index(idx, table_name="promotion_translations", schema="public")
    op.drop_table("promotion_translations", schema="public")

    for idx in (
        "ix_promotions_store_status_ends_at",
        "ix_promotions_tenant_store_status_surface",
        "ix_promotions_coupon_id",
        "ix_promotions_ends_at",
        "ix_promotions_surface",
        "ix_promotions_store_id",
        "ix_promotions_tenant_id",
    ):
        op.drop_index(idx, table_name="promotions", schema="public")
    op.drop_table("promotions", schema="public")
