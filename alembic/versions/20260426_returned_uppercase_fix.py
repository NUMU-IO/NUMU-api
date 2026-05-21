"""Add RETURNED (uppercase) to orderstatus enum.

Revision ID: returned_upper_20260426
Revises: returned_orderstatus_20260425
Create Date: 2026-04-26

The earlier migration (20260425_add_returned_to_orderstatus.py) added
``'returned'`` (lowercase) to the ``orderstatus`` Postgres enum, but
the OrderModel column does not configure ``values_callable`` so
SQLAlchemy serialises the member NAME — i.e. ``'RETURNED'`` (uppercase)
— matching every other value in this enum (``PENDING``, ``CONFIRMED``,
``SHIPPED`` …). The PATCH /orders/{id}/status path therefore 500s with:

    invalid input value for enum orderstatus: "RETURNED"

Postgres can't drop enum values, so we additively add the uppercase
form. The lowercase form remains unused (idempotent ``IF NOT EXISTS``
keeps the migration replay-safe). All other enum members stay as-is.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "returned_upper_20260426"
down_revision: str | None = "returned_orderstatus_20260425"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE must run outside a transaction in older
    # PostgreSQL releases; alembic auto-commits each ``op.execute``
    # block when transaction_per_migration is False (project default
    # for enum migrations — see prior migrations in this directory).
    op.execute("ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'RETURNED'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a
    # no-op on purpose. The orphaned uppercase value is harmless if a
    # later release switches to lowercase via ``values_callable``.
    pass
