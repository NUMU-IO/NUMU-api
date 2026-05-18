"""Add (store_id, phone) lookup index on customers.

Storefront guest checkout now treats the phone number as the source of
truth for identity: a returning guest whose phone matches an existing
customer in the same store reuses that row instead of spawning a new
``Guest`` record per order. The check runs on every guest checkout, so
the lookup needs to be cheap.

    ix_customers_store_phone
        public.customers (store_id, phone)
        WHERE phone IS NOT NULL
        → CustomerRepository.get_by_phone(store_id, phone_e164)

Partial (phone IS NOT NULL) because most rows still have NULL phones
(registered customers who signed up without one, plus all pre-fix
guests). Non-unique because households legitimately share numbers and
because there's no clean way to dedup historic rows in a migration.

CONCURRENTLY so we don't block writes on a populated customers table.

Revision ID: customer_phone_idx_20260518
Revises: is_internal_20260723
"""

from collections.abc import Sequence

from alembic import op

revision: str = "customer_phone_idx_20260518"
down_revision: str | None = "is_internal_20260723"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_customers_store_phone
        ON public.customers (store_id, phone)
        WHERE phone IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.ix_customers_store_phone")
