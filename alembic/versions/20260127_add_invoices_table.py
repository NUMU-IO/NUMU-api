"""Add invoices table for ETA e-invoicing.

Revision ID: add_invoices_table
Revises: c0e6c0c21b45
Create Date: 2026-01-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_invoices_table"
down_revision: Union[str, None] = "2d2b2176a338"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create invoice type enum
    invoicetype_enum = postgresql.ENUM(
        "I", "C", "D",
        name="invoicetype",
        schema="public",
    )
    invoicetype_enum.create(op.get_bind(), checkfirst=True)

    # Create invoice status enum
    invoicestatus_enum = postgresql.ENUM(
        "draft", "pending", "submitted", "accepted", "rejected", "cancelled",
        name="invoicestatus",
        schema="public",
    )
    invoicestatus_enum.create(op.get_bind(), checkfirst=True)

    # Create invoices table
    op.create_table(
        "invoices",
        # Primary key and tenant
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),

        # Foreign keys
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),

        # Invoice identification
        sa.Column("invoice_number", sa.String(50), nullable=False),
        sa.Column("internal_id", sa.String(100), nullable=True),

        # Type and status
        sa.Column(
            "invoice_type",
            postgresql.ENUM("I", "C", "D", name="invoicetype", schema="public", create_type=False),
            nullable=False,
            server_default="I",
        ),
        sa.Column(
            "status",
            postgresql.ENUM("draft", "pending", "submitted", "accepted", "rejected", "cancelled", name="invoicestatus", schema="public", create_type=False),
            nullable=False,
            server_default="draft",
        ),

        # Dates
        sa.Column("date_issued", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),

        # Parties (JSON)
        sa.Column("seller", postgresql.JSONB, nullable=False),
        sa.Column("buyer", postgresql.JSONB, nullable=False),

        # Currency
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("exchange_rate", sa.Integer, nullable=False, server_default="100"),

        # Line items (JSON)
        sa.Column("line_items", postgresql.JSONB, nullable=False, server_default="[]"),

        # Totals (in cents)
        sa.Column("subtotal", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_discount", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_taxes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extra_discount", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),

        # ETA submission details
        sa.Column("eta_uuid", sa.String(100), nullable=True),
        sa.Column("eta_long_id", sa.String(255), nullable=True),
        sa.Column("eta_submission_id", sa.String(100), nullable=True),
        sa.Column("eta_internal_id", sa.String(100), nullable=True),
        sa.Column("eta_hash", sa.String(255), nullable=True),
        sa.Column("eta_status_code", sa.String(50), nullable=True),
        sa.Column("eta_status_message", sa.Text, nullable=True),

        # QR Code
        sa.Column("qr_code_data", sa.Text, nullable=True),
        sa.Column("qr_code_image", sa.Text, nullable=True),

        # Signature
        sa.Column("signature", sa.Text, nullable=True),
        sa.Column("signature_type", sa.String(50), nullable=True),
        sa.Column("signature_timestamp", sa.DateTime(timezone=True), nullable=True),

        # Related documents
        sa.Column("related_invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("original_invoice_number", sa.String(50), nullable=True),

        # Notes
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("notes_ar", sa.Text, nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["public.stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["public.orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_id"], ["public.customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["related_invoice_id"], ["public.invoices.id"], ondelete="SET NULL"),

        schema="public",
    )

    # Create indexes
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"], schema="public")
    op.create_index("ix_invoices_store_id", "invoices", ["store_id"], schema="public")
    op.create_index("ix_invoices_order_id", "invoices", ["order_id"], schema="public")
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"], schema="public")
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"], unique=True, schema="public")
    op.create_index("ix_invoices_status", "invoices", ["status"], schema="public")
    op.create_index("ix_invoices_eta_uuid", "invoices", ["eta_uuid"], schema="public")
    op.create_index("ix_invoices_date_issued", "invoices", ["date_issued"], schema="public")


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_invoices_date_issued", table_name="invoices", schema="public")
    op.drop_index("ix_invoices_eta_uuid", table_name="invoices", schema="public")
    op.drop_index("ix_invoices_status", table_name="invoices", schema="public")
    op.drop_index("ix_invoices_invoice_number", table_name="invoices", schema="public")
    op.drop_index("ix_invoices_customer_id", table_name="invoices", schema="public")
    op.drop_index("ix_invoices_order_id", table_name="invoices", schema="public")
    op.drop_index("ix_invoices_store_id", table_name="invoices", schema="public")
    op.drop_index("ix_invoices_tenant_id", table_name="invoices", schema="public")

    # Drop table
    op.drop_table("invoices", schema="public")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS public.invoicestatus")
    op.execute("DROP TYPE IF EXISTS public.invoicetype")
