"""App registry: versions, OAuth clients, catalog curation.

Revision ID: app_registry_20260919
Revises: partner_accounts_20260919
Create Date: 2026-09-19

Phase 3 of docs/Plans/apps-developer-work. Additive only:
- ``apps.listing_flags`` (JSONB, default {}) and ``apps.category``;
- ``app_versions``: each uploaded ``numu.app.json`` and its review state;
- ``app_oauth_clients``: a Partner App's client id and hashed secret.

Existing rows need nothing: first-party apps (``developer_id`` NULL) stay in
the catalog whatever ``listing_flags`` says.

Downgrade drops the two tables and the two columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "app_registry_20260919"
down_revision: str | Sequence[str] | None = "partner_accounts_20260919"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.add_column(
        "apps",
        sa.Column(
            "listing_flags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="public",
    )
    op.add_column(
        "apps", sa.Column("category", sa.String(40), nullable=True), schema="public"
    )

    op.create_table(
        "app_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("release_notes", postgresql.JSONB(), nullable=True),
        sa.Column("review_notes", postgresql.JSONB(), nullable=True),
        sa.Column("review_checklist", postgresql.JSONB(), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("app_id", "version", name="uq_app_versions_app_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'in_review', 'changes_requested', "
            "'rejected', 'approved', 'published', 'superseded')",
            name="ck_app_versions_status",
        ),
        schema="public",
    )
    op.create_index(
        "ix_app_versions_status", "app_versions", ["status"], schema="public"
    )

    op.create_table(
        "app_oauth_clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.apps.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("client_id", sa.String(64), nullable=False, unique=True),
        sa.Column("client_secret_hash", sa.String(128), nullable=False),
        sa.Column("secret_rotated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("app_oauth_clients", schema="public")
    op.drop_table("app_versions", schema="public")
    op.drop_column("apps", "category", schema="public")
    op.drop_column("apps", "listing_flags", schema="public")
