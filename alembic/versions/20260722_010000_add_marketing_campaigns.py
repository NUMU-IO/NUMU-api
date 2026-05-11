"""Add marketing_campaigns + enums — Phase 8.6.

Revision ID: marketing_campaigns_20260722
Revises: bogo_tiered_20260715
Create Date: 2026-07-22

Phase 8.6 of the Shopify-parity roadmap. One new table for email +
SMS broadcast campaigns. WhatsApp campaigns keep their existing
dedicated table; the hub union-joins them for the Campaigns page.

State-machine in the entity (DRAFT → SCHEDULED → SENDING → COMPLETED
| FAILED | CANCELED). Stock movement-style audit timestamps for each
transition.

Partial index on `(scheduled_at) WHERE status='scheduled' AND
scheduled_at IS NOT NULL` keeps the Celery sweep query trivially
cheap — most campaigns at rest are DRAFT or terminal.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from alembic import op

revision: str = "marketing_campaigns_20260722"
down_revision: str | None = "bogo_tiered_20260715"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    campaign_channel = ENUM(
        "email", "sms", name="campaignchannel", create_type=False
    )
    campaign_channel.create(op.get_bind(), checkfirst=True)

    campaign_status = ENUM(
        "draft",
        "scheduled",
        "sending",
        "completed",
        "failed",
        "canceled",
        name="campaignstatus",
        create_type=False,
    )
    campaign_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "marketing_campaigns",
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
        sa.Column("channel", campaign_channel, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "status",
            campaign_status,
            nullable=False,
            server_default="draft",
        ),
        sa.Column("template_id", UUID(as_uuid=True), nullable=True),
        sa.Column("inline_subject", sa.String(255), nullable=True),
        sa.Column("inline_body", sa.Text, nullable=True),
        sa.Column("segment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("audience_filter", JSONB, nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "delivered_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
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
        schema="public",
    )
    op.create_index(
        "ix_campaigns_tenant",
        "marketing_campaigns",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_campaigns_store_status",
        "marketing_campaigns",
        ["store_id", "status"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_campaigns_scheduled",
        "marketing_campaigns",
        ["scheduled_at"],
        unique=False,
        schema="public",
        postgresql_where=sa.text(
            "status = 'scheduled' AND scheduled_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_campaigns_scheduled",
        table_name="marketing_campaigns",
        schema="public",
    )
    op.drop_index(
        "ix_campaigns_store_status",
        table_name="marketing_campaigns",
        schema="public",
    )
    op.drop_index(
        "ix_campaigns_tenant", table_name="marketing_campaigns", schema="public"
    )
    op.drop_table("marketing_campaigns", schema="public")
    sa.Enum(name="campaignstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="campaignchannel").drop(op.get_bind(), checkfirst=True)
