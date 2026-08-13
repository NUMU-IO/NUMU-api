"""Push device registrations (PWA Phase 2).

ONE table for BOTH push clients:

- ``webpush`` — the merchant-hub PWA (VAPID / RFC 8291). Carries ``p256dh``
  and ``auth``, the per-subscription encryption material.
- ``expo``    — numu-merchant-app. Carries an Expo push token in ``endpoint``
  and no encryption keys (Expo handles its own transport).

The mobile app has been POSTing to ``/auth/me/push-token`` since before this
table existed; the endpoint 404'd and the app swallowed the error. Building a
second, mobile-only table would have meant two schemas, two fan-outs and two
places to prune dead endpoints for one product behaviour.

``endpoint`` is UNIQUE: browsers re-issue the same endpoint on every
``subscribe()`` call, so without it a merchant accumulates one row per page
load. RLS mirrors every other tenant-scoped table.

Revision ID: device_registrations_20260808
Revises: sub_reminder_20260802
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "device_registrations_20260808"
down_revision: str | Sequence[str] | None = "sub_reminder_20260802"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_registrations",
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
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        # FCM/Mozilla endpoints routinely exceed 300 chars.
        sa.Column("endpoint", sa.String(1024), nullable=False),
        sa.Column("p256dh", sa.String(256), nullable=True),
        sa.Column("auth", sa.String(128), nullable=True),
        # Drives notification language — an AR merchant must not get EN push.
        sa.Column("locale", sa.String(8), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("sound", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        # Soft revoke, so "who was notified when" stays answerable after logout.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        "uq_device_registrations_endpoint",
        "device_registrations",
        ["endpoint"],
        schema="public",
    )
    op.create_index(
        "idx_device_registrations_tenant_user_active",
        "device_registrations",
        ["tenant_id", "user_id", "revoked_at"],
        schema="public",
    )
    op.create_index(
        "ix_public_device_registrations_tenant_id",
        "device_registrations",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_public_device_registrations_user_id",
        "device_registrations",
        ["user_id"],
        schema="public",
    )

    # ─── RLS ────────────────────────────────────────────────────────────────
    # Same shape as every other tenant-scoped table: the app sets
    # `app.current_tenant` per request via TenantMiddleware, and the policy
    # confines every row to it. A push fan-out must not be able to reach a
    # device belonging to another merchant.
    op.execute("ALTER TABLE public.device_registrations ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_device_registrations
        ON public.device_registrations
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_device_registrations "
        "ON public.device_registrations"
    )
    op.drop_index(
        "ix_public_device_registrations_user_id",
        table_name="device_registrations",
        schema="public",
    )
    op.drop_index(
        "ix_public_device_registrations_tenant_id",
        table_name="device_registrations",
        schema="public",
    )
    op.drop_index(
        "idx_device_registrations_tenant_user_active",
        table_name="device_registrations",
        schema="public",
    )
    op.drop_constraint(
        "uq_device_registrations_endpoint",
        "device_registrations",
        schema="public",
        type_="unique",
    )
    op.drop_table("device_registrations", schema="public")
