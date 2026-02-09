"""Create waitlist and feedback tables for beta launch.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-07

Adds:
- public.waitlist — beta merchant signup waitlist
- public.feedback — beta merchant feedback system
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Waitlist status enum ---
    waitliststatus = postgresql.ENUM(
        "pending",
        "invited",
        "converted",
        name="waitliststatus",
        schema="public",
        create_type=False,
    )
    waitliststatus.create(op.get_bind(), checkfirst=True)

    # --- Feedback category enum ---
    feedbackcategory = postgresql.ENUM(
        "bug",
        "feature_request",
        "usability",
        "performance",
        "payment",
        "general",
        name="feedbackcategory",
        schema="public",
        create_type=False,
    )
    feedbackcategory.create(op.get_bind(), checkfirst=True)

    # --- Waitlist table ---
    op.create_table(
        "waitlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column(
            "status",
            waitliststatus,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("priority_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("referral_code", sa.String(20), nullable=True),
        sa.Column("referred_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("referral_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("invite_code", sa.String(64), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
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
        "ix_waitlist_email",
        "waitlist",
        ["email"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_waitlist_status",
        "waitlist",
        ["status"],
        schema="public",
    )
    op.create_index(
        "ix_waitlist_priority_score",
        "waitlist",
        ["priority_score"],
        schema="public",
    )
    op.create_index(
        "ix_waitlist_referral_code",
        "waitlist",
        ["referral_code"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_waitlist_invite_code",
        "waitlist",
        ["invite_code"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_waitlist_referred_by",
        "waitlist",
        ["referred_by"],
        schema="public",
    )

    # --- Feedback table ---
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", feedbackcategory, nullable=False),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("contact_ok", sa.Boolean, nullable=False, server_default="true"),
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
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_feedback_rating"),
        schema="public",
    )

    op.create_index(
        "ix_feedback_store_id",
        "feedback",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_feedback_user_id",
        "feedback",
        ["user_id"],
        schema="public",
    )
    op.create_index(
        "ix_feedback_category",
        "feedback",
        ["category"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("feedback", schema="public")
    op.drop_table("waitlist", schema="public")

    op.execute("DROP TYPE IF EXISTS public.feedbackcategory")
    op.execute("DROP TYPE IF EXISTS public.waitliststatus")
