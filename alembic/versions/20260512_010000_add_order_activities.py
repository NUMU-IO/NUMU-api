"""Add order_activities table for staff comments + persisted system events.

Revision ID: order_activities_20260512
Revises: marketing_campaigns_20260722
Create Date: 2026-05-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "order_activities_20260512"
# Pre-existing dual-head: marketing_campaigns_20260722 and b404e12b9871 both
# sat as heads. Listing both here makes this migration also act as the merge
# point, so we don't need a separate empty merge revision.
down_revision: tuple[str, ...] = (
    "marketing_campaigns_20260722",
    "b404e12b9871",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    orderactivitykind = PG_ENUM(
        "COMMENT",
        "SYSTEM_EVENT",
        name="orderactivitykind",
        schema="public",
        create_type=False,
    )
    orderactivitykind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "order_activities",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("kind", orderactivitykind, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=True, server_default="{}"),
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
        schema="public",
    )

    op.create_index(
        "idx_order_activities_order_created",
        "order_activities",
        ["order_id", sa.text("created_at DESC")],
        schema="public",
    )
    op.create_index(
        "idx_order_activities_store_created",
        "order_activities",
        ["store_id", sa.text("created_at DESC")],
        schema="public",
    )
    op.create_index(
        "idx_order_activities_kind",
        "order_activities",
        ["kind"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_order_activities_kind",
        table_name="order_activities",
        schema="public",
    )
    op.drop_index(
        "idx_order_activities_store_created",
        table_name="order_activities",
        schema="public",
    )
    op.drop_index(
        "idx_order_activities_order_created",
        table_name="order_activities",
        schema="public",
    )

    op.drop_table("order_activities", schema="public")

    sa.Enum(name="orderactivitykind", schema="public").drop(
        op.get_bind(), checkfirst=True
    )
