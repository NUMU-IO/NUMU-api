"""whatsapp_gowa_devices: per-store GOWA device registry

Backs the second WhatsApp transport (go-whatsapp-web-multidevice). GOWA hosts
many logged-in accounts in one instance and picks between them with an
``X-Device-Id`` header; this table is the store -> device mapping that keeps the
backend from ever sending one merchant's message from another's number.

Additive only. Nothing reads this table until a store is explicitly switched to
the GOWA provider from the admin backoffice, so applying it changes no
behaviour.

Revision ID: wa_gowa_dev_20260808
Revises: sub_reminder_20260802
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers. Kept short: `alembic_version.version_num` is
# VARCHAR(32) and a longer id crashes the post-upgrade UPDATE.
revision = "wa_gowa_dev_20260808"
down_revision = "sub_reminder_20260802"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_gowa_devices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("consent_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consent_acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True
        ),
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
    )

    # Outbound hot path: "which device sends for this store", on every message.
    op.create_index(
        "ix_wa_gowa_device_store_active",
        "whatsapp_gowa_devices",
        ["store_id", "is_active"],
        schema="public",
    )
    # Inbound webhooks arrive keyed by device, so the reverse lookup matters too.
    op.create_index(
        "ix_wa_gowa_device_device_id",
        "whatsapp_gowa_devices",
        ["device_id"],
        schema="public",
    )
    # At most ONE active device per store. Enforced in the database rather than
    # in code: two active rows would make the sending number depend on row
    # ordering, i.e. a merchant's customers could see messages arrive from two
    # different numbers at random. A partial unique index still allows any
    # number of historical (inactive) rows.
    op.create_index(
        "uq_wa_gowa_device_one_active_per_store",
        "whatsapp_gowa_devices",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        schema="public",
    )
    # A GOWA device id is one physical WhatsApp session; it must not be shared
    # by two stores.
    op.create_index(
        "uq_wa_gowa_device_active_device_id",
        "whatsapp_gowa_devices",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_wa_gowa_device_active_device_id",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.drop_index(
        "uq_wa_gowa_device_one_active_per_store",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.drop_index(
        "ix_wa_gowa_device_device_id",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.drop_index(
        "ix_wa_gowa_device_store_active",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.drop_table("whatsapp_gowa_devices", schema="public")
