"""Add shipping_zones, shipping_zone_governorates, shipping_rates tables.

Introduces the normalized shipping configuration model:
  * shipping_zones — merchant-defined zone per store (grouping of
    governorates, with per-zone COD + ETA settings).
  * shipping_zone_governorates — M2M linking zones to canonical
    ISO-3166-2 governorate codes. Single active zone per governorate
    per store (enforced at app layer; partial unique indexes can't
    reference another table via EXISTS).
  * shipping_rates — 1..N rates per zone, type-specific `config` JSONB.

Also adds nullable snapshot FKs to `orders`: `shipping_zone_id` and
`shipping_rate_id`, both `ON DELETE SET NULL` so order history survives
later zone/rate cleanup.

RLS: the three new tables carry `tenant_id` and get the standard
four-policy bundle + admin_bypass, same as `20260424_add_instapay_tables.py`.

Revision ID: shipping_zones_20260424
Revises: instapay_tables_20260424
Create Date: 2026-04-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "shipping_zones_20260424"
down_revision: str | None = "instapay_tables_20260424"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES = [
    "shipping_zones",
    "shipping_zone_governorates",
    "shipping_rates",
]


def _apply_rls(table: str) -> None:
    """Apply the standard tenant_isolation + admin_bypass policy bundle."""
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
    # ── shipping_zones ──────────────────────────────────────────────
    op.create_table(
        "shipping_zones",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
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
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_ar", sa.String(100), nullable=True),
        sa.Column(
            "estimated_days_min",
            sa.SmallInteger,
            nullable=False,
            server_default="2",
        ),
        sa.Column(
            "estimated_days_max",
            sa.SmallInteger,
            nullable=False,
            server_default="5",
        ),
        sa.Column(
            "cod_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "cod_fee_cents",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="public",
    )
    op.create_index(
        "ix_shipping_zones_tenant_id",
        "shipping_zones",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_shipping_zones_store_id",
        "shipping_zones",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_shipping_zones_store_active",
        "shipping_zones",
        ["store_id", "is_active"],
        schema="public",
    )

    # ── shipping_zone_governorates (M2M) ─────────────────────────────
    op.create_table(
        "shipping_zone_governorates",
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.shipping_zones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("governorate_code", sa.String(10), nullable=False),
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
        sa.PrimaryKeyConstraint(
            "zone_id",
            "governorate_code",
            name="pk_shipping_zone_gov",
        ),
        schema="public",
    )
    op.create_index(
        "ix_shipping_zone_governorates_tenant_id",
        "shipping_zone_governorates",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_shipping_zone_governorates_store_id",
        "shipping_zone_governorates",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_shipping_zone_governorates_store_gov",
        "shipping_zone_governorates",
        ["store_id", "governorate_code"],
        schema="public",
    )

    # ── shipping_rates ──────────────────────────────────────────────
    op.create_table(
        "shipping_rates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.shipping_zones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rate_type", sa.String(32), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("label_ar", sa.String(100), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="public",
    )
    op.create_index(
        "ix_shipping_rates_tenant_id",
        "shipping_rates",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_shipping_rates_zone_id",
        "shipping_rates",
        ["zone_id"],
        schema="public",
    )
    op.create_index(
        "ix_shipping_rates_zone_active",
        "shipping_rates",
        ["zone_id", "is_active"],
        schema="public",
    )

    # ── orders: add shipping snapshot FKs ──────────────────────────
    op.add_column(
        "orders",
        sa.Column(
            "shipping_zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "public.shipping_zones.id",
                ondelete="SET NULL",
                name="fk_orders_shipping_zone_id",
            ),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column(
            "shipping_rate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "public.shipping_rates.id",
                ondelete="SET NULL",
                name="fk_orders_shipping_rate_id",
            ),
            nullable=True,
        ),
        schema="public",
    )
    op.create_index(
        "ix_orders_shipping_zone_id",
        "orders",
        ["shipping_zone_id"],
        schema="public",
    )
    op.create_index(
        "ix_orders_shipping_rate_id",
        "orders",
        ["shipping_rate_id"],
        schema="public",
    )

    # ── RLS ─────────────────────────────────────────────────────────
    for table in _RLS_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _RLS_TABLES:
        _drop_rls(table)

    op.drop_index("ix_orders_shipping_rate_id", table_name="orders", schema="public")
    op.drop_index("ix_orders_shipping_zone_id", table_name="orders", schema="public")
    op.drop_constraint(
        "fk_orders_shipping_rate_id", "orders", type_="foreignkey", schema="public"
    )
    op.drop_constraint(
        "fk_orders_shipping_zone_id", "orders", type_="foreignkey", schema="public"
    )
    op.drop_column("orders", "shipping_rate_id", schema="public")
    op.drop_column("orders", "shipping_zone_id", schema="public")

    op.drop_index(
        "ix_shipping_rates_zone_active", table_name="shipping_rates", schema="public"
    )
    op.drop_index(
        "ix_shipping_rates_zone_id", table_name="shipping_rates", schema="public"
    )
    op.drop_index(
        "ix_shipping_rates_tenant_id", table_name="shipping_rates", schema="public"
    )
    op.drop_table("shipping_rates", schema="public")

    op.drop_index(
        "ix_shipping_zone_governorates_store_gov",
        table_name="shipping_zone_governorates",
        schema="public",
    )
    op.drop_index(
        "ix_shipping_zone_governorates_store_id",
        table_name="shipping_zone_governorates",
        schema="public",
    )
    op.drop_index(
        "ix_shipping_zone_governorates_tenant_id",
        table_name="shipping_zone_governorates",
        schema="public",
    )
    op.drop_table("shipping_zone_governorates", schema="public")

    op.drop_index(
        "ix_shipping_zones_store_active", table_name="shipping_zones", schema="public"
    )
    op.drop_index(
        "ix_shipping_zones_store_id", table_name="shipping_zones", schema="public"
    )
    op.drop_index(
        "ix_shipping_zones_tenant_id", table_name="shipping_zones", schema="public"
    )
    op.drop_table("shipping_zones", schema="public")
