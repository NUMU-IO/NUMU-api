"""add message_logs table

Revision ID: a1b2c3d4e5f6
Revises: 9a8b7c6d5e4f
Create Date: 2026-02-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7e6d5c4b3a2"
down_revision: Union[str, None] = "9a8b7c6d5e4f"
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Create enums                                                     #
    # ------------------------------------------------------------------ #
    messagedirection_enum = postgresql.ENUM(
        "inbound",
        "outbound",
        name="messagedirection",
        schema="public",
        create_type=False,
    )
    messagedirection_enum.create(op.get_bind(), checkfirst=True)

    messagestatus_enum = postgresql.ENUM(
        "queued",
        "sent",
        "delivered",
        "read",
        "failed",
        name="messagestatus",
        schema="public",
        create_type=False,
    )
    messagestatus_enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------ #
    # 2. Create table                                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "message_logs",
        sa.Column("id", sa.UUID(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column(
            "direction",
            messagedirection_enum,
            nullable=False,
        ),
        sa.Column("template_name", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "status",
            messagestatus_enum,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["public.stores.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("message_id", name="uq_message_logs_message_id"),
        schema="public",
    )

    # ------------------------------------------------------------------ #
    # 3. Create indexes                                                   #
    # ------------------------------------------------------------------ #
    op.create_index(
        "ix_message_logs_tenant_id",
        "message_logs",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_message_logs_store_id",
        "message_logs",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_message_logs_message_id",
        "message_logs",
        ["message_id"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_message_logs_phone",
        "message_logs",
        ["phone"],
        schema="public",
    )

    # ------------------------------------------------------------------ #
    # 4. Enable Row-Level Security                                        #
    # ------------------------------------------------------------------ #
    conn = op.get_bind()

    conn.exec_driver_sql(
        "ALTER TABLE public.message_logs ENABLE ROW LEVEL SECURITY"
    )
    conn.exec_driver_sql(
        "ALTER TABLE public.message_logs FORCE ROW LEVEL SECURITY"
    )

    # -- Tenant isolation policies ------------------------------------ #
    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_select
        ON public.message_logs
        FOR SELECT
        USING (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_insert
        ON public.message_logs
        FOR INSERT
        WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_update
        ON public.message_logs
        FOR UPDATE
        USING (tenant_id = public.get_current_tenant_id())
        WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_delete
        ON public.message_logs
        FOR DELETE
        USING (tenant_id = public.get_current_tenant_id())
    """)

    # -- Admin bypass policy ------------------------------------------ #
    conn.exec_driver_sql("""
        CREATE POLICY admin_bypass
        ON public.message_logs
        FOR ALL
        USING (public.is_rls_bypassed())
        WITH CHECK (public.is_rls_bypassed())
    """)


def downgrade() -> None:
    conn = op.get_bind()

    # Drop RLS policies
    for policy in (
        "admin_bypass",
        "tenant_isolation_delete",
        "tenant_isolation_update",
        "tenant_isolation_insert",
        "tenant_isolation_select",
    ):
        conn.exec_driver_sql(
            f"DROP POLICY IF EXISTS {policy} ON public.message_logs"
        )

    conn.exec_driver_sql(
        "ALTER TABLE public.message_logs DISABLE ROW LEVEL SECURITY"
    )

    # Drop indexes
    op.drop_index("ix_message_logs_phone", table_name="message_logs", schema="public")
    op.drop_index("ix_message_logs_message_id", table_name="message_logs", schema="public")
    op.drop_index("ix_message_logs_store_id", table_name="message_logs", schema="public")
    op.drop_index("ix_message_logs_tenant_id", table_name="message_logs", schema="public")

    # Drop table
    op.drop_table("message_logs", schema="public")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS public.messagestatus")
    op.execute("DROP TYPE IF EXISTS public.messagedirection")
