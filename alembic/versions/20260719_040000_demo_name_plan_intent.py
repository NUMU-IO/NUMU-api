"""Demo lead capture + signup plan intent.

- ``tenants.demo_name`` — the visitor's name captured by the landing
  "Try a Demo" form, next to the existing ``demo_email``, so every demo
  is attributable to a person (sales follow-up).
- ``users.plan_intent`` — which pricing card the visitor clicked on the
  landing before registering (``payg`` / ``starter`` / ``pro``). A payg
  intent auto-activates Pay as you Grow at store creation; paid intents
  are kept for attribution/preselection.

Purely additive nullable columns.

Revision ID: demo_name_intent_20260719
Revises: golive_exempt_20260719
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "demo_name_intent_20260719"
down_revision: str | Sequence[str] | None = "golive_exempt_20260719"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("demo_name", sa.String(120), nullable=True),
        schema="public",
    )
    op.add_column(
        "users",
        sa.Column("plan_intent", sa.String(20), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("users", "plan_intent", schema="public")
    op.drop_column("tenants", "demo_name", schema="public")
