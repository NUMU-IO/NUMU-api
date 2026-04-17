"""Add omnichannel inbox tables.

Revision ID: omnichannel_inbox_v1
Revises: d759b0f72f37
Create Date: 2026-04-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "omnichannel_inbox_v1"
down_revision: str | None = "d759b0f72f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── ENUM types ──────────────────────────────────────────────────────
    op.execute("CREATE TYPE channel_enum AS ENUM ('facebook', 'instagram', 'whatsapp')")
    op.execute(
        "CREATE TYPE connection_status_enum AS ENUM ('active', 'expired', 'revoked', 'error')"
    )
    op.execute("CREATE TYPE thread_status_enum AS ENUM ('open', 'resolved', 'spam')")
    op.execute("CREATE TYPE message_direction_enum AS ENUM ('inbound', 'outbound')")
    op.execute(
        "CREATE TYPE message_type_enum AS ENUM ('text', 'image', 'video', 'audio', 'document', 'sticker', 'template', 'product', 'system')"
    )
    op.execute(
        "CREATE TYPE message_status_enum AS ENUM ('sent', 'delivered', 'read', 'failed', 'received')"
    )
    op.execute(
        "CREATE TYPE template_category_enum AS ENUM ('MARKETING', 'UTILITY', 'AUTHENTICATION')"
    )
    op.execute(
        "CREATE TYPE template_status_enum AS ENUM ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED', 'PAUSED', 'DISABLED')"
    )
    op.execute(
        "CREATE TYPE catalog_sync_status_enum AS ENUM ('pending', 'synced', 'failed', 'removed')"
    )
    op.execute("CREATE TYPE webhook_provider_enum AS ENUM ('meta', 'whatsapp')")
    op.execute(
        "CREATE TYPE webhook_status_enum AS ENUM ('received', 'processing', 'processed', 'failed', 'dead')"
    )

    # ─── channel_connections ─────────────────────────────────────────────
    op.create_table(
        "channel_connections",
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
            index=True,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("external_account_id", sa.Text, nullable=True),
        sa.Column("external_account_name", sa.Text, nullable=True),
        sa.Column("external_phone_number_id", sa.Text, nullable=True),
        sa.Column("encrypted_credentials", sa.dialects.postgresql.BYTEA, nullable=True),
        sa.Column("credential_key_id", sa.String(100), nullable=True),
        sa.Column("scopes", sa.dialects.postgresql.ARRAY(sa.String), nullable=True),
        sa.Column("webhook_subscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("meta_business_id", sa.Text, nullable=True),
        sa.Column("catalog_id", sa.Text, nullable=True),
        sa.Column("payment_configuration_id", sa.Text, nullable=True),
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
    op.create_unique_constraint(
        "uq_channel_connections_store_channel",
        "channel_connections",
        ["store_id", "channel", "external_account_id"],
        schema="public",
    )

    # ─── message_threads ─────────────────────────────────────────────────
    op.create_table(
        "message_threads",
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
            index=True,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column(
            "channel_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.channel_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("external_participant_id", sa.Text, nullable=False),
        sa.Column("participant_name", sa.Text, nullable=True),
        sa.Column("participant_avatar_url", sa.Text, nullable=True),
        sa.Column("participant_phone_e164", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_preview", sa.Text, nullable=True),
        sa.Column("unread_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "assigned_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, nullable=True),
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
    op.create_unique_constraint(
        "uq_message_threads_connection_participant",
        "message_threads",
        ["channel_connection_id", "external_participant_id"],
        schema="public",
    )
    op.create_index(
        "ix_message_threads_store_last_message",
        "message_threads",
        ["store_id", sa.text("last_message_at DESC")],
        schema="public",
    )
    op.create_index(
        "ix_message_threads_store_status",
        "message_threads",
        ["store_id", "status", sa.text("last_message_at DESC")],
        schema="public",
    )

    # ─── channel_messages ────────────────────────────────────────────────
    op.create_table(
        "channel_messages",
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
            "thread_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.message_threads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_message_id", sa.Text, nullable=True),
        sa.Column("external_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sender_external_id", sa.Text, nullable=True),
        sa.Column("type", sa.String(20), nullable=False, server_default="text"),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("attachment_url", sa.Text, nullable=True),
        sa.Column("attachment_mime", sa.Text, nullable=True),
        sa.Column("template_name", sa.Text, nullable=True),
        sa.Column("template_payload", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "product_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("error_code", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("raw_payload", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "ix_channel_messages_created",
        "channel_messages",
        ["created_at"],
        schema="public",
    )
    op.create_index(
        "ix_channel_messages_external_id",
        "channel_messages",
        ["channel", "external_message_id"],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
        schema="public",
    )

    # ─── whatsapp_templates ───────────────────────────────────────────────
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
            index=True,
        ),
        sa.Column(
            "channel_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.channel_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("external_template_id", sa.Text, nullable=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("components", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_unique_constraint(
        "uq_whatsapp_templates_connection_name_lang",
        "whatsapp_templates",
        ["channel_connection_id", "name", "language"],
        schema="public",
    )

    # ─── catalog_mappings ─────────────────────────────────────────────────
    op.create_table(
        "catalog_mappings",
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
            index=True,
        ),
        sa.Column(
            "product_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.channel_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("external_catalog_id", sa.Text, nullable=True),
        sa.Column("external_product_id", sa.Text, nullable=True),
        sa.Column(
            "sync_status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
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
    op.create_unique_constraint(
        "uq_catalog_mappings_product_connection",
        "catalog_mappings",
        ["product_id", "channel_connection_id"],
        schema="public",
    )

    # ─── webhook_events ────────────────────────────────────────────────────
    op.create_table(
        "webhook_events",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("event_type", sa.Text, nullable=True),
        sa.Column("external_id", sa.Text, nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("signature", sa.Text, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
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
    op.create_unique_constraint(
        "uq_webhook_events_external_id",
        "webhook_events",
        ["provider", "external_id"],
        schema="public",
    )

    # ─── capi_events ───────────────────────────────────────────────────────
    op.create_table(
        "capi_events",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_name", sa.Text, nullable=False),
        sa.Column(
            "event_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_code", sa.Integer, nullable=True),
        sa.Column("response_body", sa.dialects.postgresql.JSONB, nullable=True),
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

    # ─── RLS policies (mirror order_policy pattern) ────────────────────────
    op.execute("""
        CREATE POLICY tenant_isolation_channel_connections ON public.channel_connections
        USING (tenant_id::text = current_setting('app.current_tenant', true)::text)
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_message_threads ON public.message_threads
        USING (tenant_id::text = current_setting('app.current_tenant', true)::text)
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_channel_messages ON public.channel_messages
        USING (tenant_id::text = current_setting('app.current_tenant', true)::text)
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_whatsapp_templates ON public.whatsapp_templates
        USING (tenant_id::text = current_setting('app.current_tenant', true)::text)
    """)
    op.execute("""
        CREATE POLICY tenant_isolation_catalog_mappings ON public.catalog_mappings
        USING (tenant_id::text = current_setting('app.current_tenant', true)::text)
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_channel_connections ON public.channel_connections"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_message_threads ON public.message_threads"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_channel_messages ON public.channel_messages"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_whatsapp_templates ON public.whatsapp_templates"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_catalog_mappings ON public.catalog_mappings"
    )

    op.drop_table("capi_events", schema="public")
    op.drop_table("webhook_events", schema="public")
    op.drop_table("catalog_mappings", schema="public")
    op.drop_table("whatsapp_templates", schema="public")
    op.drop_table("channel_messages", schema="public")
    op.drop_table("message_threads", schema="public")
    op.drop_table("channel_connections", schema="public")

    op.execute("DROP TYPE IF EXISTS webhook_status_enum")
    op.execute("DROP TYPE IF EXISTS webhook_provider_enum")
    op.execute("DROP TYPE IF EXISTS catalog_sync_status_enum")
    op.execute("DROP TYPE IF EXISTS template_status_enum")
    op.execute("DROP TYPE IF EXISTS template_category_enum")
    op.execute("DROP TYPE IF EXISTS message_status_enum")
    op.execute("DROP TYPE IF EXISTS message_type_enum")
    op.execute("DROP TYPE IF EXISTS message_direction_enum")
    op.execute("DROP TYPE IF EXISTS thread_status_enum")
    op.execute("DROP TYPE IF EXISTS connection_status_enum")
    op.execute("DROP TYPE IF EXISTS channel_enum")
