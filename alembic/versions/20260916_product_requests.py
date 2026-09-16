"""Product requests — a shopper asking for something the store does not list.

Revision ID: product_requests_20260916
Revises: book_commerce_20260912
Create Date: 2026-09-16

Bookshops field this constantly: "do you have this edition?", usually with a
photo of a cover from somewhere else. It arrived in a DM or a WhatsApp thread
and was lost by the next day. This is the record of that ask — who wants it,
what they wrote, the photos they attached — so the merchant can answer it with
a price and see which ones are still unanswered.

`status` is a short string rather than an enum: the merchant's workflow is
theirs, and widening an enum costs a migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "product_requests_20260916"
down_revision: str | None = "book_commerce_20260912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column(
            "images",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("locale", sa.String(8), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="public",
        if_not_exists=True,
    )
    op.create_index(
        "ix_product_requests_store_id",
        "product_requests",
        ["store_id"],
        schema="public",
        if_not_exists=True,
    )
    op.create_index(
        "ix_product_requests_store_status",
        "product_requests",
        ["store_id", "status"],
        schema="public",
        if_not_exists=True,
    )
    op.create_index(
        "ix_product_requests_store_created",
        "product_requests",
        ["store_id", "created_at"],
        schema="public",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_requests_store_created",
        table_name="product_requests",
        schema="public",
        if_exists=True,
    )
    op.drop_index(
        "ix_product_requests_store_status",
        table_name="product_requests",
        schema="public",
        if_exists=True,
    )
    op.drop_index(
        "ix_product_requests_store_id",
        table_name="product_requests",
        schema="public",
        if_exists=True,
    )
    op.drop_table("product_requests", schema="public", if_exists=True)
