"""Add otp_codes table (backend-025 / spec 015 — WhatsApp OTP).

Stores the HMAC-hashed code + per-phone rate-limit counters. Cleartext
codes are NEVER stored.

Revision ID: otp_codes_20260511
Revises: courier_stats_20260511
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "otp_codes_20260511"
down_revision: str | None = "courier_stats_20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "otp_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("phone_hash", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column(
            "language",
            sa.String(2),
            server_default=sa.text("'ar'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempts_left",
            sa.Integer,
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_send_at", sa.DateTime(timezone=True), nullable=True),
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
        "ix_otp_codes_phone_hash",
        "otp_codes",
        ["phone_hash"],
        schema="public",
    )
    op.create_index(
        "ix_otp_codes_store_phone",
        "otp_codes",
        ["store_id", "phone_hash"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_otp_codes_store_phone",
        table_name="otp_codes",
        schema="public",
    )
    op.drop_index(
        "ix_otp_codes_phone_hash",
        table_name="otp_codes",
        schema="public",
    )
    op.drop_table("otp_codes", schema="public")
