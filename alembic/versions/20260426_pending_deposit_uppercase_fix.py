"""Add PENDING_DEPOSIT (uppercase) to orderstatus enum.

Revision ID: pending_dep_upper_20260426
Revises: returned_upper_20260426
Create Date: 2026-04-26

The earlier migration (20260425_cod_deposit_fields.py) added
``'pending_deposit'`` (lowercase) to the ``orderstatus`` Postgres
enum, but the OrderModel's ``status`` column doesn't configure
``values_callable``, so SQLAlchemy emits the member NAME — i.e.
``'PENDING_DEPOSIT'`` (uppercase) — matching every other value in
this enum (``PENDING``, ``CONFIRMED``, ``SHIPPED`` …). Submitting a
COD-with-deposit checkout therefore 500s with:

    invalid input value for enum orderstatus: "PENDING_DEPOSIT"

Same root cause as the ``RETURNED`` fix from ``returned_upper_20260426``;
this migration mirrors that pattern. Postgres can't drop enum values,
so we additively add the uppercase form. The unused lowercase form
remains harmless.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "pending_dep_upper_20260426"
down_revision: str | None = "returned_upper_20260426"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'PENDING_DEPOSIT'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a
    # no-op on purpose.
    pass
