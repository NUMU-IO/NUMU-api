"""Partner accounts: outside developers who build on NUMU.

Revision ID: partner_accounts_20260919
Revises: numu_apps_20260919
Create Date: 2026-09-19

Phase 2 of docs/Plans/apps-developer-work. Additive: one new table.

Backfill: theme upload and the marketplace developer routes now require an
approved partner (``require_approved_partner``). Every user who already owns a
marketplace theme or created a theme gets an APPROVED partner row here, so no
existing theme developer loses access. ``agreement_version`` is
``legacy-theme-developer`` for them; they accept the real agreement the first
time it is versioned.

Downgrade drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "partner_accounts_20260919"
down_revision: str | Sequence[str] | None = "numu_apps_20260919"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "partner_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=True),
        sa.Column("country", sa.String(2), nullable=False, server_default="EG"),
        sa.Column("website_url", sa.String(2048), nullable=True),
        sa.Column("support_email", sa.String(255), nullable=False),
        sa.Column("support_phone", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("agreement_version", sa.String(32), nullable=True),
        sa.Column("agreement_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agreement_accepted_ip", sa.String(64), nullable=True),
        sa.Column("review_notes", postgresql.JSONB(), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'suspended')",
            name="ck_partner_accounts_status",
        ),
        sa.CheckConstraint(
            "kind IN ('individual', 'company')", name="ck_partner_accounts_kind"
        ),
        schema="public",
    )
    op.create_index(
        "ix_partner_accounts_status", "partner_accounts", ["status"], schema="public"
    )

    op.execute(
        """
        INSERT INTO public.partner_accounts (
            id, user_id, kind, display_name, support_email, status,
            agreement_version, reviewed_at, created_at, updated_at
        )
        SELECT gen_random_uuid(), u.id, 'individual',
               COALESCE(NULLIF(TRIM(CONCAT(u.first_name, ' ', u.last_name)), ''),
                        u.email),
               u.email, 'approved', 'legacy-theme-developer', now(), now(), now()
        FROM public.users u
        WHERE u.id IN (
            SELECT developer_id FROM public.marketplace_themes
            WHERE developer_id IS NOT NULL
            UNION
            SELECT created_by FROM public.themes WHERE created_by IS NOT NULL
        )
        ON CONFLICT (user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("partner_accounts", schema="public")
