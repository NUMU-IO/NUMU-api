"""Add social_posts table for tracking imported social media posts.

Revision ID: ee7788990022
Revises: ee7788990011
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "ee7788990022"
down_revision = "ee7788990011"

branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_posts",
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
            index=True,
        ),
        sa.Column(
            "social_connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.social_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("platform_post_id", sa.String(255), nullable=False),
        sa.Column("image_url", sa.Text, nullable=True),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("likes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer, nullable=False, server_default="0"),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suggested_name", sa.String(255), nullable=True),
        sa.Column("suggested_name_ar", sa.String(255), nullable=True),
        sa.Column("suggested_price", sa.Integer, nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        # Unique constraint: one import record per platform post per connection
        sa.UniqueConstraint(
            "social_connection_id",
            "platform_post_id",
            name="uq_social_post_per_connection",
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("social_posts", schema="public")
