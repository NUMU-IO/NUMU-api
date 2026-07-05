"""Add whatsapp_access_requests table (platform WhatsApp access gate).

Revision ID: whatsapp_access_20260705
Revises: merge_heads_20260704
Create Date: 2026-07-05

Per-store entitlement gate for the WhatsApp channel: a merchant requests access,
a platform admin approves/rejects, and can later disable/re-enable. One row per
store. The ``whatsappaccessstatus`` PG enum stores the UPPERCASE member names to
match the SQLAlchemy mapping (``Enum(WhatsAppAccessStatus)`` with no
``values_callable``), consistent with the sibling ``accessrequeststatus`` type.
See memory: enum-details (values_callable rule).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "whatsapp_access_20260705"
down_revision: str | None = "merge_heads_20260704"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    status_enum = postgresql.ENUM(
        "PENDING",
        "APPROVED",
        "REJECTED",
        "DISABLED",
        name="whatsappaccessstatus",
        schema="public",
    )
    status_enum.create(bind, checkfirst=True)

    op.create_table(
        "whatsapp_access_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "APPROVED",
                "REJECTED",
                "DISABLED",
                name="whatsappaccessstatus",
                schema="public",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("requester_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("expected_volume", sa.String(length=64), nullable=True),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("store_id", name="uq_whatsapp_access_store"),
        schema="public",
    )
    op.create_index(
        "ix_whatsapp_access_requests_tenant",
        "whatsapp_access_requests",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_whatsapp_access_requests_status",
        "whatsapp_access_requests",
        ["status"],
        schema="public",
    )

    # Grandfather-in existing WhatsApp users so introducing the gate does not
    # disrupt stores that already connected a number / turned WhatsApp on.
    # We only auto-approve stores with *active* WhatsApp evidence — an active
    # BYO credential, or a store that explicitly went through connect/signup
    # (settings.whatsapp.enabled / is_configured). Stores that merely carry the
    # default (all-true) notification toggles from onboarding are NOT matched,
    # so they still go through the request → approve flow. ``owner_id`` becomes
    # the nominal requester. The service_type/service_name enums are stored as
    # lowercase values (values_callable), hence 'whatsapp'/'whatsapp_business'.
    op.execute(
        """
        INSERT INTO public.whatsapp_access_requests
            (id, store_id, tenant_id, status, requester_user_id,
             review_reason, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            s.id,
            s.tenant_id,
            'APPROVED',
            s.owner_id,
            'Auto-approved by backfill: store was already using WhatsApp '
                || 'before the access gate was introduced.',
            now(),
            now()
        FROM public.stores s
        WHERE
            -- explicitly configured / connected on the store settings
            (s.settings #>> '{whatsapp,enabled}') = 'true'
            OR (s.settings #>> '{whatsapp,is_configured}') = 'true'
            -- active BYO Meta WABA credential
            OR EXISTS (
                SELECT 1
                FROM public.service_credentials c
                WHERE c.tenant_id = s.tenant_id
                  AND c.service_type = 'whatsapp'
                  AND c.service_name = 'whatsapp_business'
                  AND c.is_active = true
            )
            -- has actually sent/received WhatsApp messages. message_logs is the
            -- WhatsApp message log (phone + template_name + Meta message_id), so
            -- this catches active NUMU shared-number (platform_managed) stores
            -- too — the primary population — not just BYO.
            OR EXISTS (
                SELECT 1
                FROM public.message_logs m
                WHERE m.store_id = s.id
            )
        ON CONFLICT (store_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_access_requests_status",
        table_name="whatsapp_access_requests",
        schema="public",
    )
    op.drop_index(
        "ix_whatsapp_access_requests_tenant",
        table_name="whatsapp_access_requests",
        schema="public",
    )
    op.drop_table("whatsapp_access_requests", schema="public")
    postgresql.ENUM(name="whatsappaccessstatus", schema="public").drop(
        op.get_bind(), checkfirst=True
    )
