"""NUMU Agent knowledge base (two layers) + RLS on Layer B

Revision ID: agent_knowledge_001_20260626
Revises: agent_mvp_001_20260626
Create Date: 2026-06-26 13:00:00

Layer A (numu_knowledge_*) is shared platform documentation — no tenant_id, no
RLS (contains no merchant data). Layer B (tenant_knowledge_*) is per-tenant and
RLS-isolated. Embeddings are stored as JSONB float arrays in this MVP; a later
migration converts them to pgvector `vector(1024)` + an ANN index for scale.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "agent_knowledge_001_20260626"
down_revision: str | None = "agent_mvp_001_20260626"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ["tenant_knowledge_docs", "tenant_knowledge_chunks"]


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _ts_cols() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # ── Layer A (shared) ────────────────────────────────────────────────────
    op.create_table(
        "numu_knowledge_docs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("source", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        *_ts_cols(),
        schema="public",
    )
    op.create_table(
        "numu_knowledge_chunks",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "doc_id",
            _uuid(),
            sa.ForeignKey("public.numu_knowledge_docs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        *_ts_cols(),
        schema="public",
    )
    op.create_index(
        "ix_numu_knowledge_chunks_doc_id",
        "numu_knowledge_chunks",
        ["doc_id"],
        schema="public",
    )

    # ── Layer B (per-tenant) ────────────────────────────────────────────────
    op.create_table(
        "tenant_knowledge_docs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            _uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        *_ts_cols(),
        schema="public",
    )
    op.create_index(
        "ix_tenant_knowledge_docs_tenant_id",
        "tenant_knowledge_docs",
        ["tenant_id"],
        schema="public",
    )
    op.create_table(
        "tenant_knowledge_chunks",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            _uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_id",
            _uuid(),
            sa.ForeignKey("public.tenant_knowledge_docs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        *_ts_cols(),
        schema="public",
    )
    op.create_index(
        "ix_tenant_knowledge_chunks_tenant_id",
        "tenant_knowledge_chunks",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_tenant_knowledge_chunks_doc_id",
        "tenant_knowledge_chunks",
        ["doc_id"],
        schema="public",
    )

    conn = op.get_bind()
    for table in _TENANT_TABLES:
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


def downgrade() -> None:
    op.drop_table("tenant_knowledge_chunks", schema="public")
    op.drop_table("tenant_knowledge_docs", schema="public")
    op.drop_table("numu_knowledge_chunks", schema="public")
    op.drop_table("numu_knowledge_docs", schema="public")
