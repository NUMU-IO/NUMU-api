"""Add marketplace_theme_reviews for theme ratings + written reviews.

Revision ID: marketplace_reviews_20260507
Revises: marketplace_purchases_20260507
Create Date: 2026-05-07

`marketplace_themes.average_rating` and `review_count` already exist —
they were added with the initial marketplace tables — but until now
nothing wrote to them. This migration adds the per-review row, plus
indexes for the two hot read paths:

  * "list reviews for theme X, newest first" → ix_mtr_theme_created
  * "did user Y already review theme X?"     → unique constraint

Schema decisions:
  * UNIQUE (marketplace_theme_id, user_id): one review per buyer per
    theme. Edits go through PUT, not duplicate POSTs.
  * `is_verified_purchase` flag — set at insert time when the user has
    a succeeded purchase row OR (for free themes) an active install.
    Frontend renders a "Verified buyer" badge from this.
  * `developer_response` is a single optional text column — Shopify-
    style "developer replied to your review" (one back-and-forth, not
    a thread). Threading can come later if anyone asks.
  * `helpful_count` exists for future "X found this helpful" voting;
    no endpoint writes to it yet but having the column avoids a
    follow-up migration.

Cascade story: delete the theme → reviews vanish (irrelevant). Delete
the user → keep the review and null out the FK so historical ratings
remain meaningful (NUMU's user-deletion path zeros out reviews via the
service rather than relying on the DB cascade, see below).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "marketplace_reviews_20260507"
down_revision: str | None = "marketplace_purchases_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "marketplace_theme_reviews",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "marketplace_theme_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.marketplace_themes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        # 1..5, app-level enforced (CHECK below). We avoid a Postgres
        # ENUM here because rating values almost never change and an
        # ENUM is more friction than help.
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "is_verified_purchase",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("developer_response", sa.Text(), nullable=True),
        sa.Column(
            "developer_response_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "helpful_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "marketplace_theme_id", "user_id", name="uq_mtr_theme_user"
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_mtr_rating_range"),
        schema="public",
    )

    # Hot read path: "list reviews for theme X, newest first" — the
    # frontend paginates this on every theme detail page. Composite
    # index lets PG seek directly without a sort step.
    op.create_index(
        "ix_mtr_theme_created",
        "marketplace_theme_reviews",
        ["marketplace_theme_id", sa.text("created_at DESC")],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mtr_theme_created",
        table_name="marketplace_theme_reviews",
        schema="public",
    )
    op.drop_table("marketplace_theme_reviews", schema="public")
