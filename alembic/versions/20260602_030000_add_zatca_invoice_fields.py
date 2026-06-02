"""Add ZATCA (Fatoora) e-invoicing fields to invoices.

Revision ID: zatca_invoice_20260602
Revises: saudi_gateways_20260602
Create Date: 2026-06-02

Phase 4 of multi-market support. Mirrors the existing ``eta_*`` block with a
``zatca_*`` block for Saudi e-invoicing. The shared ``qr_code_*`` and
``signature_*`` columns (already present) carry the TLV-encoded QR and the
XAdES signature, so only the ZATCA submission-metadata columns are new.

All nullable — Egyptian invoices simply leave them null.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "zatca_invoice_20260602"
down_revision: str = "saudi_gateways_20260602"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = (
    ("zatca_uuid", sa.String(length=100)),
    ("zatca_invoice_hash", sa.String(length=255)),
    ("zatca_previous_hash", sa.String(length=255)),
    ("zatca_submission_id", sa.String(length=100)),
    ("zatca_status_code", sa.String(length=50)),
    ("zatca_status_message", sa.Text()),
)


def upgrade() -> None:
    for name, col_type in _COLUMNS:
        op.add_column(
            "invoices",
            sa.Column(name, col_type, nullable=True),
            schema="public",
        )
    op.create_index(
        "ix_invoices_zatca_uuid",
        "invoices",
        ["zatca_uuid"],
        unique=False,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_zatca_uuid", table_name="invoices", schema="public")
    for name, _ in reversed(_COLUMNS):
        op.drop_column("invoices", name, schema="public")
