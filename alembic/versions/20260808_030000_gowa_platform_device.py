"""whatsapp_gowa_devices: allow a PLATFORM device shared by every store

The BYO case is one merchant, one number, one device — enforced by a partial
unique index on store_id and another on device_id.

The platform case is the opposite and is how the shared NUMU number already
works on Meta: ONE number sends on behalf of every store that hasn't brought
its own. That needs a device row with no store, reachable as a fallback, and it
needs the device_id uniqueness constraint to stop meaning "one store per
device".

So: store_id becomes nullable, `is_platform` marks the shared row, and the
uniqueness rules are re-scoped — at most one active platform device overall,
and still at most one active device per store.

Revision ID: wa_gowa_plat_20260808
Revises: wa_gowa_reply_20260808
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "wa_gowa_plat_20260808"
down_revision = "wa_gowa_reply_20260808"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_gowa_devices",
        sa.Column(
            "is_platform",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="public",
    )
    # The platform device belongs to no single store or tenant.
    op.alter_column(
        "whatsapp_gowa_devices",
        "store_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
        schema="public",
    )
    op.alter_column(
        "whatsapp_gowa_devices",
        "tenant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
        schema="public",
    )

    # device_id was globally unique among active rows, which encoded "one store
    # per device". The platform row is shared by design, so uniqueness now means
    # "one active row per device_id" — still preventing a duplicate registration
    # of the same physical session, without forbidding sharing.
    op.drop_index(
        "uq_wa_gowa_device_active_device_id",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.create_index(
        "uq_wa_gowa_device_active_device_id",
        "whatsapp_gowa_devices",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        schema="public",
    )

    # At most ONE active platform device. Two would make the sending number
    # depend on row ordering for every store on the shared path.
    op.create_index(
        "uq_wa_gowa_device_one_active_platform",
        "whatsapp_gowa_devices",
        ["is_platform"],
        unique=True,
        postgresql_where=sa.text("is_active AND is_platform"),
        schema="public",
    )

    # The per-store uniqueness must now ignore the storeless platform row.
    op.drop_index(
        "uq_wa_gowa_device_one_active_per_store",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.create_index(
        "uq_wa_gowa_device_one_active_per_store",
        "whatsapp_gowa_devices",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND store_id IS NOT NULL"),
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_wa_gowa_device_one_active_platform",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.drop_index(
        "uq_wa_gowa_device_one_active_per_store",
        table_name="whatsapp_gowa_devices",
        schema="public",
    )
    op.create_index(
        "uq_wa_gowa_device_one_active_per_store",
        "whatsapp_gowa_devices",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        schema="public",
    )
    op.drop_column("whatsapp_gowa_devices", "is_platform", schema="public")
