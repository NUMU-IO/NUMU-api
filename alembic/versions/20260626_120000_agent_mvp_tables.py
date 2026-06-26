"""NUMU Agent MVP tables + RLS

Revision ID: agent_mvp_001_20260626
Revises: merge_v3_wa_heads_20260730
Create Date: 2026-06-26 12:00:00

Creates the four tenant-scoped Agent tables (conversations, turns, action
proposals, audit logs) and enables Row-Level Security on each, reusing the
existing helper functions `public.get_current_tenant_id()` and
`public.is_rls_bypassed()` created by the base RLS migration.

Audit logs are append-only: RLS grants SELECT + INSERT (plus admin bypass) but
NO update/delete policy, so the rows cannot be mutated by tenant sessions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "agent_mvp_001_20260626"
down_revision: str | None = "merge_v3_wa_heads_20260730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that get the full tenant-isolation policy set (SELECT/INSERT/UPDATE/DELETE).
_CRUD_TABLES = ["agent_conversations", "agent_turns", "agent_action_proposals"]


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            _uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "staff_id",
            _uuid(),
            sa.ForeignKey("public.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
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
        "ix_agent_conversations_tenant_id",
        "agent_conversations",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_agent_conversations_staff_id",
        "agent_conversations",
        ["staff_id"],
        schema="public",
    )

    op.create_table(
        "agent_turns",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            _uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            _uuid(),
            sa.ForeignKey("public.agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("model_used", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
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
        "ix_agent_turns_tenant_id", "agent_turns", ["tenant_id"], schema="public"
    )
    op.create_index(
        "ix_agent_turns_conversation_id",
        "agent_turns",
        ["conversation_id"],
        schema="public",
    )

    op.create_table(
        "agent_action_proposals",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            _uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            _uuid(),
            sa.ForeignKey("public.agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "diff",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("based_on_theme_version", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
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
        "ix_agent_action_proposals_tenant_id",
        "agent_action_proposals",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_agent_action_proposals_conversation_id",
        "agent_action_proposals",
        ["conversation_id"],
        schema="public",
    )

    op.create_table(
        "agent_audit_logs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            _uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "staff_id",
            _uuid(),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("conversation_id", _uuid(), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "before_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "after_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("model_used", sa.String(128), nullable=True),
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
        "ix_agent_audit_logs_tenant_id",
        "agent_audit_logs",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_agent_audit_logs_staff_id",
        "agent_audit_logs",
        ["staff_id"],
        schema="public",
    )

    conn = op.get_bind()

    # Full tenant-isolation policies for the read/write tables.
    for table in _CRUD_TABLES:
        conn.exec_driver_sql(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;")
        conn.exec_driver_sql(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;")
        conn.exec_driver_sql(
            f"CREATE POLICY tenant_isolation_select ON public.{table} FOR SELECT "
            f"USING (tenant_id = public.get_current_tenant_id());"
        )
        conn.exec_driver_sql(
            f"CREATE POLICY tenant_isolation_insert ON public.{table} FOR INSERT "
            f"WITH CHECK (tenant_id = public.get_current_tenant_id());"
        )
        conn.exec_driver_sql(
            f"CREATE POLICY tenant_isolation_update ON public.{table} FOR UPDATE "
            f"USING (tenant_id = public.get_current_tenant_id()) "
            f"WITH CHECK (tenant_id = public.get_current_tenant_id());"
        )
        conn.exec_driver_sql(
            f"CREATE POLICY tenant_isolation_delete ON public.{table} FOR DELETE "
            f"USING (tenant_id = public.get_current_tenant_id());"
        )
        conn.exec_driver_sql(
            f"CREATE POLICY admin_bypass ON public.{table} USING (public.is_rls_bypassed() = true) "
            f"WITH CHECK (public.is_rls_bypassed() = true);"
        )

    # Audit log: append-only — SELECT + INSERT (+ admin bypass), no UPDATE/DELETE policy.
    conn.exec_driver_sql(
        "ALTER TABLE public.agent_audit_logs ENABLE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql(
        "ALTER TABLE public.agent_audit_logs FORCE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql(
        "CREATE POLICY tenant_isolation_select ON public.agent_audit_logs FOR SELECT "
        "USING (tenant_id = public.get_current_tenant_id());"
    )
    conn.exec_driver_sql(
        "CREATE POLICY tenant_isolation_insert ON public.agent_audit_logs FOR INSERT "
        "WITH CHECK (tenant_id = public.get_current_tenant_id());"
    )
    conn.exec_driver_sql(
        "CREATE POLICY admin_bypass ON public.agent_audit_logs USING (public.is_rls_bypassed() = true) "
        "WITH CHECK (public.is_rls_bypassed() = true);"
    )


def downgrade() -> None:
    op.drop_table("agent_audit_logs", schema="public")
    op.drop_table("agent_action_proposals", schema="public")
    op.drop_table("agent_turns", schema="public")
    op.drop_table("agent_conversations", schema="public")
