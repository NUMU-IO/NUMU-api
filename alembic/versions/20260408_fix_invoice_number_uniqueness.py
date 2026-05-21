"""fix invoice_number uniqueness to be per-store, not global

Revision ID: 8e1f6a2b4d5c
Revises: 7d0e5f1a3c4b
Create Date: 2026-04-08

The original migration created a GLOBAL unique index on invoice_number,
but get_next_invoice_number() generates numbers from a per-store count
(e.g. ``INV-2026-000006``). Two different stores both with 5 invoices
would both try to insert ``INV-2026-000006`` and the second would fail
with an IntegrityError, silently swallowed by the invoice creation
paths. This migration switches the constraint to be per-store, which
matches the numbering scheme.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8e1f6a2b4d5c"
down_revision: str | None = "7d0e5f1a3c4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the global unique index on invoice_number.
    op.drop_index(
        "ix_invoices_invoice_number",
        table_name="invoices",
        schema="public",
    )
    # Recreate as a non-unique index so single-column lookups stay fast.
    op.create_index(
        "ix_invoices_invoice_number",
        "invoices",
        ["invoice_number"],
        unique=False,
        schema="public",
    )
    # Add the composite unique index that matches the per-store numbering.
    op.create_index(
        "uq_invoices_store_invoice_number",
        "invoices",
        ["store_id", "invoice_number"],
        unique=True,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_invoices_store_invoice_number",
        table_name="invoices",
        schema="public",
    )
    op.drop_index(
        "ix_invoices_invoice_number",
        table_name="invoices",
        schema="public",
    )
    op.create_index(
        "ix_invoices_invoice_number",
        "invoices",
        ["invoice_number"],
        unique=True,
        schema="public",
    )
