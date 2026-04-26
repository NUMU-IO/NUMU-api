"""Add email_templates and email_logs tables.

Two new public-schema tables backing the per-store transactional email
customization feature:

  * ``email_templates`` — store-level overrides keyed by
    ``(store_id, event_type, language)`` (unique).
  * ``email_logs`` — audit-trail of every send attempt; FK to
    ``email_templates.id`` with ``ON DELETE SET NULL`` so deleting a
    template preserves history.

Revision ID: email_templates_20260426
Revises: store_business_hours_20260425
Create Date: 2026-04-26 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "email_templates_20260426"
down_revision: str | None = "store_business_hours_20260425"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
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
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column(
            "language",
            sa.String(length=10),
            nullable=False,
            server_default="ar",
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("from_name", sa.String(length=255), nullable=True),
        sa.Column("reply_to", sa.String(length=255), nullable=True),
        sa.Column("extra_data", JSONB, nullable=True),
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
        "idx_email_templates_store",
        "email_templates",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_email_templates_store_event_lang",
        "email_templates",
        ["store_id", "event_type", "language"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "idx_email_templates_tenant",
        "email_templates",
        ["tenant_id"],
        schema="public",
    )

    op.create_table(
        "email_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
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
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.email_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "used_custom_template",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("extra_data", JSONB, nullable=True),
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
        "idx_email_logs_store",
        "email_logs",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_email_logs_message_id",
        "email_logs",
        ["message_id"],
        schema="public",
    )
    op.create_index(
        "idx_email_logs_tenant",
        "email_logs",
        ["tenant_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("idx_email_logs_tenant", table_name="email_logs", schema="public")
    op.drop_index("idx_email_logs_message_id", table_name="email_logs", schema="public")
    op.drop_index("idx_email_logs_store", table_name="email_logs", schema="public")
    op.drop_table("email_logs", schema="public")

    op.drop_index(
        "idx_email_templates_tenant",
        table_name="email_templates",
        schema="public",
    )
    op.drop_index(
        "idx_email_templates_store_event_lang",
        table_name="email_templates",
        schema="public",
    )
    op.drop_index(
        "idx_email_templates_store",
        table_name="email_templates",
        schema="public",
    )
    op.drop_table("email_templates", schema="public")
