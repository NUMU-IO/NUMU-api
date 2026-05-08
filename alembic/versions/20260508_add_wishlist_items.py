"""Add wishlist_items table (Phase 4.5).

Revision ID: wishlist_items_20260508
Revises: product_search_tsv_20260508
Create Date: 2026-05-08

Server-backed wishlist storage so authenticated customers see their
saved items across devices. Guests still get the SDK's localStorage
fallback; on login, the merge endpoint moves session-keyed rows into
the customer's namespace (the same pattern carts use).

Schema decisions:
  * UNIQUE (customer_id, session_id, product_id, variant_id):
    idempotent saves. A customer can save "Hoodie any variant"
    AND "Hoodie / Black" as separate rows because variant_id null
    differs from variant_id value in Postgres uniqueness.
  * Either customer_id or session_id is set, never both — enforced
    by the route layer (cart_owner pattern).
  * No partial index on (customer_id) / (session_id) because both
    are already the lead column on full b-tree indexes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "wishlist_items_20260508"
down_revision: str | None = "product_search_tsv_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wishlist_items",
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
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("variant_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "customer_id",
            "session_id",
            "product_id",
            "variant_id",
            name="uq_wishlist_target",
        ),
        schema="public",
    )

    op.create_index(
        "ix_wishlist_customer",
        "wishlist_items",
        ["customer_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_wishlist_session",
        "wishlist_items",
        ["session_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_wishlist_store",
        "wishlist_items",
        ["store_id"],
        unique=False,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wishlist_store",
        table_name="wishlist_items",
        schema="public",
    )
    op.drop_index(
        "ix_wishlist_session",
        table_name="wishlist_items",
        schema="public",
    )
    op.drop_index(
        "ix_wishlist_customer",
        table_name="wishlist_items",
        schema="public",
    )
    op.drop_table("wishlist_items", schema="public")
