"""Add WhatsApp templates, conversations, campaigns, and campaign recipients tables.

Revision ID: wa0413b2c3d4
Revises: d759b0f72f37
Create Date: 2026-04-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "wa0413b2c3d4"
down_revision: str | None = "d759b0f72f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── WhatsApp Templates ─────────────────────────────────────────
    op.create_table(
        "whatsapp_templates",
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
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("meta_template_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("language", sa.String(10), nullable=False, server_default="ar"),
        sa.Column("category", sa.String(20), nullable=False, server_default="UTILITY"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("header_type", sa.String(20), nullable=True),
        sa.Column("header_content", sa.String(500), nullable=True),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("footer_text", sa.String(60), nullable=True),
        sa.Column("buttons", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
        "idx_wa_templates_store",
        "whatsapp_templates",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_wa_templates_store_name",
        "whatsapp_templates",
        ["store_id", "name", "language"],
        unique=True,
        schema="public",
    )

    # ─── WhatsApp Conversations ─────────────────────────────────────
    op.create_table(
        "whatsapp_conversations",
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
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("customer_phone", sa.String(20), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=True),
        sa.Column("customer_profile_pic_url", sa.String(500), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_preview", sa.String(255), nullable=True),
        sa.Column("last_message_direction", sa.String(10), nullable=True),
        sa.Column("unread_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "assigned_to",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=True),
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
        "idx_wa_conv_store_last_msg",
        "whatsapp_conversations",
        ["store_id", "last_message_at"],
        schema="public",
    )
    op.create_index(
        "idx_wa_conv_store_phone",
        "whatsapp_conversations",
        ["store_id", "customer_phone"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "idx_wa_conv_store_status",
        "whatsapp_conversations",
        ["store_id", "status"],
        schema="public",
    )

    # ─── WhatsApp Campaigns ─────────────────────────────────────────
    op.create_table(
        "whatsapp_campaigns",
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
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "template_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.whatsapp_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("audience_filter", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("template_params", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("delivered_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("read_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
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
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "idx_wa_campaigns_store",
        "whatsapp_campaigns",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_wa_campaigns_status",
        "whatsapp_campaigns",
        ["store_id", "status"],
        schema="public",
    )

    # ─── WhatsApp Campaign Recipients ───────────────────────────────
    op.create_table(
        "whatsapp_campaign_recipients",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "campaign_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.whatsapp_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.create_index(
        "idx_wa_camp_recip_campaign",
        "whatsapp_campaign_recipients",
        ["campaign_id"],
        schema="public",
    )
    op.create_index(
        "idx_wa_camp_recip_message_id",
        "whatsapp_campaign_recipients",
        ["message_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("whatsapp_campaign_recipients", schema="public")
    op.drop_table("whatsapp_campaigns", schema="public")
    op.drop_table("whatsapp_conversations", schema="public")
    op.drop_table("whatsapp_templates", schema="public")
