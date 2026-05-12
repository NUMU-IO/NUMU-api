"""Add DRAFT to orderstatus enum for Draft Orders feature.

Revision ID: draft_orderstatus_20260512
Revises: order_activities_20260512
Create Date: 2026-05-12

Drafts are merchant-saved orders not yet billable / visible to the
customer. We extend the existing ``orderstatus`` Postgres enum rather
than introducing a separate ``draft_orders`` table — drafts share the
same record shape and the same UUID space, just with status=DRAFT and
no fulfillment / payment side-effects.

Like every other value in this enum, we add the UPPERCASE form because
the OrderModel's ``status`` column doesn't configure ``values_callable``
(SQLAlchemy emits the member NAME). Same rationale as
``pending_dep_upper_20260426``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "draft_orderstatus_20260512"
down_revision: str | None = "order_activities_20260512"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'DRAFT'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a
    # no-op on purpose.
    pass
