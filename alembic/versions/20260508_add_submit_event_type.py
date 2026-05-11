"""Add `submit` to event_type_enum.

Backs the offers-v2 popup / floating-widget form-capture flow. The new
event represents a visitor completing the embedded form (email +
optional phone + consent flag) on a popup that's configured to collect
leads. Captured PII goes into the event row's `metadata` blob — no
separate submissions table — so analytics, retention, and right-to-be-
forgotten deletion stay on the single `promotion_events` path.

Revision ID: promo_submit_event_20260508
Revises: tenant_feature_flags_20260507
Create Date: 2026-05-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "promo_submit_event_20260508"
down_revision: str | None = "tenant_feature_flags_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres ENUM additions require an explicit ALTER; the SA-level
    # enum reflection in the model file picks up the new value at next
    # connection refresh.
    op.execute("ALTER TYPE public.event_type_enum ADD VALUE IF NOT EXISTS 'submit'")


def downgrade() -> None:
    # Postgres < 14 can't drop a single enum value, and the safe path
    # here is "the older app build still tolerates an unknown event
    # type sitting in the table" — readers SELECT the column as a string
    # and ignore unrecognized values. Leaving the value in place is a
    # deliberate trade-off for downgrade safety.
    pass
