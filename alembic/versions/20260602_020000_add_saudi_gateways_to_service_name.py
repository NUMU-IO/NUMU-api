"""Add Saudi payment gateways to service_name_enum.

Revision ID: saudi_gateways_20260602
Revises: add_store_country_20260602
Create Date: 2026-06-02

Phase 3 of multi-market support. Adds the Saudi payment-gateway keys to
``service_name_enum`` so merchant credentials for them can be stored in
``service_credentials``. Moyasar is wired end-to-end; the others
(HyperPay, Tabby, Tamara, STC Pay) are registered as stubs.

``ADD VALUE IF NOT EXISTS`` is idempotent and the values aren't used in
this same transaction, so it is safe within Alembic's transaction
(PostgreSQL 12+). Enum values cannot be dropped, so downgrade is a no-op.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "saudi_gateways_20260602"
down_revision: str = "add_store_country_20260602"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NEW_VALUES = ("moyasar", "hyperpay", "tabby", "tamara", "stcpay")


def upgrade() -> None:
    for value in _NEW_VALUES:
        op.execute(f"ALTER TYPE service_name_enum ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    pass
