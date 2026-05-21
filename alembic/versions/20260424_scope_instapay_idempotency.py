"""Scope payment_proofs.idempotency_key uniqueness to store.

The original unique constraint was global across every tenant, which
(a) grew unbounded because old keys were never cleared and (b) could
theoretically 409 a legitimate upload on a cross-tenant key collision.
Scoping to ``(store_id, idempotency_key)`` matches how the use case
already queries the column and lets the Celery sweeper TTL stale keys
without affecting other stores.

Revision ID: scope_instapay_idem_20260424
Revises: migrate_ship_legacy_20260424
Create Date: 2026-04-24 15:00:00.000000

Note: revision ids shortened from the original
`scope_instapay_idempotency_20260424` / `migrate_legacy_shipping_zones_20260424`
to fit Alembic's `version_num VARCHAR(32)` column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "scope_instapay_idem_20260424"
down_revision: str | None = "migrate_ship_legacy_20260424"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_payment_proofs_idempotency_key",
        "payment_proofs",
        type_="unique",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_payment_proofs_store_idempotency_key",
        "payment_proofs",
        ["store_id", "idempotency_key"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_payment_proofs_store_idempotency_key",
        "payment_proofs",
        type_="unique",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_payment_proofs_idempotency_key",
        "payment_proofs",
        ["idempotency_key"],
        schema="public",
    )
