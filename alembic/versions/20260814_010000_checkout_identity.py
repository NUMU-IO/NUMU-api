"""Phone-first checkout identity (spec: checkout-identity).

Three small pieces of schema for the phone + WhatsApp-OTP identification
layer:

- ``customers.phone_verified_at`` — when this customer last proved ownership
  of ``customers.phone`` via OTP. Nullable; NULL means "never verified".
  Deliberately a timestamp rather than a boolean so re-verification after a
  phone change is representable, and analytics can age out stale proofs.

- ``ix_customers_store_phone`` — ``get_by_phone`` is the PRIMARY dedup key at
  checkout and the identity lookup on OTP verify, but ``customers.phone`` has
  never been indexed: every lookup was a per-store scan that only got slower
  as stores grew.

- ``ix_abandoned_checkouts_phone_sweep`` (partial) — the recovery sweep
  runs every 30 minutes asking exactly "phone-bearing, anonymous,
  un-recovered rows in a last_activity window", so the index is shaped as
  that predicate over ``last_activity_at``. Most rows have no phone
  (capture is fingerprint-keyed), which keeps the partial tiny.

Revision ID: checkout_identity_20260814
Revises: device_registrations_20260808
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "checkout_identity_20260814"
down_revision: str | Sequence[str] | None = "device_registrations_20260808"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_customers_store_phone",
        "customers",
        ["store_id", "phone"],
    )
    op.create_index(
        "ix_abandoned_checkouts_phone_sweep",
        "abandoned_checkouts",
        ["last_activity_at"],
        postgresql_where=sa.text(
            "phone IS NOT NULL AND customer_id IS NULL AND recovered_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_abandoned_checkouts_phone_sweep", table_name="abandoned_checkouts"
    )
    op.drop_index("ix_customers_store_phone", table_name="customers")
    op.drop_column("customers", "phone_verified_at")
