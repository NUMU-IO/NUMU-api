"""Create promotion-related Postgres enum types.

Foundational migration for the offers-v2 / Promotions feature. Creates the
six Postgres ENUM types that the upcoming promotion tables reference.

Splitting the type creation into its own migration keeps each subsequent
table-creation revision self-contained and easy to roll back.

Revision ID: promotion_enums_20260507
Revises: merge_theme_heads_20260505
Create Date: 2026-05-07
"""

from collections.abc import Sequence

from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "promotion_enums_20260507"
down_revision: str | None = "merge_theme_heads_20260505"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ENUMS: list[tuple[str, tuple[str, ...]]] = [
    (
        "promotion_surface_enum",
        (
            "discount_code",
            "automatic",
            "announcement_bar",
            "popup",
            "floating_widget",
            "cookie_banner",
        ),
    ),
    (
        "promotion_status_enum",
        (
            "draft",
            "scheduled",
            "active",
            "paused",
            "expired",
            "archived",
        ),
    ),
    (
        "display_trigger_enum",
        (
            "on_load",
            "on_delay",
            "on_scroll_pct",
            "on_exit_intent",
            "on_add_to_cart",
            "always",
        ),
    ),
    (
        "display_frequency_enum",
        (
            "once_per_session",
            "once_per_visitor",
            "every_visit",
            "until_dismissed",
            "until_redeemed",
        ),
    ),
    (
        "target_kind_enum",
        (
            "audience",
            "product",
            "category",
            "customer_tag",
            "geo",
        ),
    ),
    (
        "event_type_enum",
        (
            "impression",
            "click",
            "dismiss",
            "redeem",
            "convert",
        ),
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in _ENUMS:
        postgresql.ENUM(
            *values,
            name=name,
            schema="public",
            create_type=True,
        ).create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name, _values in reversed(_ENUMS):
        postgresql.ENUM(
            name=name,
            schema="public",
        ).drop(bind, checkfirst=True)
