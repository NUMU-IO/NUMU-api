"""NUMU Knowledge Base (spec 002): soft-add pgvector + lifecycle + merchant notes

Revision ID: knowledge_pgvector_002_20260626
Revises: agent_knowledge_001_20260626
Create Date: 2026-06-26 14:00:00

**Soft / additive / non-breaking.** The 001 `embedding JSONB` column is KEPT. This
migration:
  1. Tries `CREATE EXTENSION vector` (guarded — logs & continues on failure).
  2. If pgvector is active, ADDS a parallel `embedding_vec vector(1024)` column to
     the four chunk tables (the JSONB column stays as fallback + backfill source).
  3. Adds lifecycle/coverage columns to the doc tables (nullable / server-default so
     existing rows stay valid).
  4. Creates `agent_tenant_notes` + the 001 RLS policy set.
  5. Backfills `embedding_vec` from JSONB and builds an HNSW cosine index (only if
     pgvector is active).
  6. Creates the `numu_knowledge_coverage` view.

`downgrade()` is lossless — it never drops the original JSONB `embedding` column.
The destructive JSONB removal is deferred to a future cleanup migration after the
pgvector path is verified in production.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "knowledge_pgvector_002_20260626"
down_revision: str | None = "agent_knowledge_001_20260626"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_CHUNK_TABLES = [
    "numu_knowledge_chunks",
    "tenant_knowledge_chunks",
]
_TENANT_NOTE_TABLE = "agent_tenant_notes"
EMBED_DIM = 1024


def _try_create_extension(conn) -> bool:
    """Attempt CREATE EXTENSION vector; never hard-fail. Returns availability."""
    try:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
    except Exception as exc:  # noqa: BLE001 — soft-add: degrade to the JSONB path
        logger.warning(
            "pgvector extension unavailable (%s); knowledge retrieval will use the "
            "JSONB fallback path until pgvector is provisioned.",
            exc,
        )
        return False
    # Confirm it actually registered.
    row = conn.exec_driver_sql(
        "SELECT 1 FROM pg_extension WHERE extname = 'vector';"
    ).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()
    has_vector = _try_create_extension(conn)

    # ── lifecycle / coverage columns on the shared doc table ────────────────
    op.add_column(
        "numu_knowledge_docs",
        sa.Column("area", sa.String(64), nullable=True),
        schema="public",
    )
    op.add_column(
        "numu_knowledge_docs",
        sa.Column(
            "source_kind", sa.String(16), nullable=False, server_default="authored"
        ),
        schema="public",
    )
    op.add_column(
        "numu_knowledge_docs",
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        schema="public",
    )
    op.add_column(
        "numu_knowledge_docs",
        sa.Column("content_hash", sa.String(64), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_numu_knowledge_docs_area", "numu_knowledge_docs", ["area"], schema="public"
    )
    op.create_index(
        "ix_numu_knowledge_docs_status",
        "numu_knowledge_docs",
        ["status"],
        schema="public",
    )

    # ── lifecycle columns on the tenant doc table ───────────────────────────
    op.add_column(
        "tenant_knowledge_docs",
        sa.Column("source_kind", sa.String(16), nullable=False, server_default="note"),
        schema="public",
    )
    op.add_column(
        "tenant_knowledge_docs",
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        schema="public",
    )
    op.add_column(
        "tenant_knowledge_docs",
        sa.Column("content_hash", sa.String(64), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_tenant_knowledge_docs_status",
        "tenant_knowledge_docs",
        ["status"],
        schema="public",
    )

    # ── additive pgvector column (only if the extension is active) ──────────
    if has_vector:
        for table in _CHUNK_TABLES:
            conn.exec_driver_sql(
                f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS embedding_vec vector({EMBED_DIM});"
            )

    # ── merchant notes table (Layer-B authoring source) + RLS ───────────────
    op.create_table(
        _TENANT_NOTE_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "author_staff_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("layer_b_doc_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        "ix_agent_tenant_notes_tenant_id",
        _TENANT_NOTE_TABLE,
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_agent_tenant_notes_store_id",
        _TENANT_NOTE_TABLE,
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_agent_tenant_notes_status", _TENANT_NOTE_TABLE, ["status"], schema="public"
    )

    # Reuse the 001 RLS policy set (public.get_current_tenant_id / is_rls_bypassed).
    conn.exec_driver_sql(
        f"ALTER TABLE public.{_TENANT_NOTE_TABLE} ENABLE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql(
        f"ALTER TABLE public.{_TENANT_NOTE_TABLE} FORCE ROW LEVEL SECURITY;"
    )
    conn.exec_driver_sql(
        f"CREATE POLICY tenant_isolation_select ON public.{_TENANT_NOTE_TABLE} FOR SELECT "
        f"USING (tenant_id = public.get_current_tenant_id());"
    )
    conn.exec_driver_sql(
        f"CREATE POLICY tenant_isolation_insert ON public.{_TENANT_NOTE_TABLE} FOR INSERT "
        f"WITH CHECK (tenant_id = public.get_current_tenant_id());"
    )
    conn.exec_driver_sql(
        f"CREATE POLICY tenant_isolation_update ON public.{_TENANT_NOTE_TABLE} FOR UPDATE "
        f"USING (tenant_id = public.get_current_tenant_id()) "
        f"WITH CHECK (tenant_id = public.get_current_tenant_id());"
    )
    conn.exec_driver_sql(
        f"CREATE POLICY tenant_isolation_delete ON public.{_TENANT_NOTE_TABLE} FOR DELETE "
        f"USING (tenant_id = public.get_current_tenant_id());"
    )
    conn.exec_driver_sql(
        f"CREATE POLICY admin_bypass ON public.{_TENANT_NOTE_TABLE} USING (public.is_rls_bypassed() = true) "
        f"WITH CHECK (public.is_rls_bypassed() = true);"
    )

    # ── backfill embedding_vec from JSONB + HNSW index (only if pgvector) ────
    if has_vector:
        for table in _CHUNK_TABLES:
            # Cast the JSONB float array → text → vector. Safe for empty/seed tables.
            conn.exec_driver_sql(
                f"UPDATE public.{table} "
                f"SET embedding_vec = (embedding::text)::vector "
                f"WHERE embedding_vec IS NULL AND embedding IS NOT NULL;"
            )
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_embedding_vec_hnsw "
                f"ON public.{table} USING hnsw (embedding_vec vector_cosine_ops);"
            )

    # ── coverage view (raw values; the app applies the staleness threshold) ─
    conn.exec_driver_sql(
        """
        CREATE OR REPLACE VIEW public.numu_knowledge_coverage AS
        SELECT
            area,
            COUNT(*) FILTER (WHERE status = 'published')        AS published_count,
            MAX(updated_at) FILTER (WHERE status = 'published') AS newest_updated_at
        FROM public.numu_knowledge_docs
        WHERE area IS NOT NULL
        GROUP BY area;
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP VIEW IF EXISTS public.numu_knowledge_coverage;")

    op.drop_table(_TENANT_NOTE_TABLE, schema="public")

    for table in _CHUNK_TABLES:
        conn.exec_driver_sql(
            f"DROP INDEX IF EXISTS public.ix_{table}_embedding_vec_hnsw;"
        )
        conn.exec_driver_sql(
            f"ALTER TABLE public.{table} DROP COLUMN IF EXISTS embedding_vec;"
        )

    op.drop_index(
        "ix_tenant_knowledge_docs_status",
        table_name="tenant_knowledge_docs",
        schema="public",
    )
    op.drop_column("tenant_knowledge_docs", "content_hash", schema="public")
    op.drop_column("tenant_knowledge_docs", "status", schema="public")
    op.drop_column("tenant_knowledge_docs", "source_kind", schema="public")

    op.drop_index(
        "ix_numu_knowledge_docs_status",
        table_name="numu_knowledge_docs",
        schema="public",
    )
    op.drop_index(
        "ix_numu_knowledge_docs_area", table_name="numu_knowledge_docs", schema="public"
    )
    op.drop_column("numu_knowledge_docs", "content_hash", schema="public")
    op.drop_column("numu_knowledge_docs", "status", schema="public")
    op.drop_column("numu_knowledge_docs", "source_kind", schema="public")
    op.drop_column("numu_knowledge_docs", "area", schema="public")
    # NOTE: the original JSONB `embedding` column is intentionally NOT dropped.
