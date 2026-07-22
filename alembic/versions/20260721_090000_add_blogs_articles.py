"""Add blogs + articles tables (Blog/Articles CMS, parity leg 1.2).

Additive: two new ``public`` tables only. No changes to existing tables,
so it's safe for production stores — they have no blog rows until a
merchant creates them. Mirrors the pages-table shape (bilingual JSONB,
tenant_id discriminator, handle unique per parent).

Revision ID: blogs_articles_20260721
Revises: 55cac6b181c2
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "blogs_articles_20260721"
down_revision: str | None = "55cac6b181c2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blogs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("handle", sa.String(length=255), nullable=False),
        sa.Column(
            "title", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "description",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_published", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.UniqueConstraint("store_id", "handle", name="uq_blogs_store_handle"),
        schema="public",
    )
    op.create_index("ix_blogs_tenant_id", "blogs", ["tenant_id"], schema="public")
    op.create_index("ix_blogs_store_id", "blogs", ["store_id"], schema="public")
    op.create_index("ix_blogs_handle", "blogs", ["handle"], schema="public")

    op.create_table(
        "articles",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blog_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.blogs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("handle", sa.String(length=255), nullable=False),
        sa.Column(
            "title", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "excerpt", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "body", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column(
            "tags", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "seo", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="draft"
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "previous_handles",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
        sa.UniqueConstraint("blog_id", "handle", name="uq_articles_blog_handle"),
        schema="public",
    )
    op.create_index("ix_articles_tenant_id", "articles", ["tenant_id"], schema="public")
    op.create_index("ix_articles_store_id", "articles", ["store_id"], schema="public")
    op.create_index("ix_articles_blog_id", "articles", ["blog_id"], schema="public")
    op.create_index("ix_articles_handle", "articles", ["handle"], schema="public")
    op.create_index("ix_articles_status", "articles", ["status"], schema="public")
    op.create_index(
        "ix_articles_published_at", "articles", ["published_at"], schema="public"
    )
    op.create_index(
        "ix_articles_store_status",
        "articles",
        ["store_id", "status"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("ix_articles_store_status", table_name="articles", schema="public")
    op.drop_index("ix_articles_published_at", table_name="articles", schema="public")
    op.drop_index("ix_articles_status", table_name="articles", schema="public")
    op.drop_index("ix_articles_handle", table_name="articles", schema="public")
    op.drop_index("ix_articles_blog_id", table_name="articles", schema="public")
    op.drop_index("ix_articles_store_id", table_name="articles", schema="public")
    op.drop_index("ix_articles_tenant_id", table_name="articles", schema="public")
    op.drop_table("articles", schema="public")
    op.drop_index("ix_blogs_handle", table_name="blogs", schema="public")
    op.drop_index("ix_blogs_store_id", table_name="blogs", schema="public")
    op.drop_index("ix_blogs_tenant_id", table_name="blogs", schema="public")
    op.drop_table("blogs", schema="public")
