"""Add VAT-inclusive pricing fields to invoices table.

Shifts the invoice schema from VAT-exclusive (subtotal + VAT + shipping
= total) to VAT-inclusive (subtotal already includes 14% VAT, customer
pays subtotal + shipping). Egyptian retail standard — merchants enter
the final price; VAT is extracted for tax reporting only.

New columns:
    * ``prices_include_vat BOOLEAN`` — discriminator for the pricing
      model used. ``true`` for rows under the new policy; ``false``
      reserved for any historical mixed-mode rows that show up.
    * ``vat_rate NUMERIC(5,2)`` — VAT percentage used (default 14.00).
    * ``vat_amount INTEGER`` — VAT extracted from subtotal, in cents.
    * ``net_amount_before_vat INTEGER`` — ``subtotal - vat_amount``.
    * ``shipping_fee INTEGER`` — shipping cost in cents, separate from
      product subtotal so the invoice template can display it on its
      own line. VAT-free in this phase.
    * ``grand_total INTEGER`` — final amount the customer pays:
      ``subtotal + shipping_fee - extra_discount``.

Backfill: existing rows are assumed to be VAT-exclusive (old model)
where ``total = subtotal + total_taxes``. We migrate them in place by
setting:
    new_subtotal = old (subtotal + total_taxes)   ← becomes VAT-inclusive
    vat_amount    = old total_taxes
    net_amount    = old subtotal
    shipping_fee  = 0  (historical orders' shipping was baked into total)
    grand_total   = old total

That keeps the visible grand total stable for existing invoices while
moving the column shape forward. Pre-existing PDFs are not regenerated.

Revision ID: invoice_vat_inclusive_20260515
Revises: funnel_event_idemp_20260514
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "invoice_vat_inclusive_20260515"
down_revision: str | None = "funnel_event_idemp_20260514"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New columns — added NULLable so the table-rewrite step is a
    # metadata-only ALTER on Postgres, then backfilled in a second pass,
    # then promoted to NOT NULL once every row has a value.
    op.add_column(
        "invoices",
        sa.Column("prices_include_vat", sa.Boolean(), nullable=True),
        schema="public",
    )
    op.add_column(
        "invoices",
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=True),
        schema="public",
    )
    op.add_column(
        "invoices",
        sa.Column("vat_amount", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "invoices",
        sa.Column("net_amount_before_vat", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "invoices",
        sa.Column("shipping_fee", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "invoices",
        sa.Column("grand_total", sa.Integer(), nullable=True),
        schema="public",
    )

    # Backfill historical rows from the old VAT-exclusive shape into
    # the new VAT-inclusive shape. ``subtotal`` is rewritten to include
    # the VAT that was previously additive so the new ``grand_total``
    # math stays consistent with the legacy ``total``.
    op.execute(
        """
        UPDATE public.invoices
        SET
            prices_include_vat    = TRUE,
            vat_rate              = 14.00,
            vat_amount            = COALESCE(total_taxes, 0),
            net_amount_before_vat = COALESCE(subtotal, 0),
            shipping_fee          = 0,
            grand_total           = COALESCE(total, 0),
            subtotal              = COALESCE(subtotal, 0) + COALESCE(total_taxes, 0)
        WHERE prices_include_vat IS NULL
        """
    )

    # Promote to NOT NULL + defaults now that every row carries values.
    op.alter_column(
        "invoices",
        "prices_include_vat",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("TRUE"),
        schema="public",
    )
    op.alter_column(
        "invoices",
        "vat_rate",
        existing_type=sa.Numeric(5, 2),
        nullable=False,
        server_default=sa.text("14.00"),
        schema="public",
    )
    op.alter_column(
        "invoices",
        "vat_amount",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
        schema="public",
    )
    op.alter_column(
        "invoices",
        "net_amount_before_vat",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
        schema="public",
    )
    op.alter_column(
        "invoices",
        "shipping_fee",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
        schema="public",
    )
    op.alter_column(
        "invoices",
        "grand_total",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
        schema="public",
    )


def downgrade() -> None:
    # Reverse the subtotal rewrite so the column shape lines up with
    # the legacy VAT-exclusive form (subtotal = net before VAT).
    op.execute(
        """
        UPDATE public.invoices
        SET subtotal = COALESCE(net_amount_before_vat, subtotal)
        WHERE prices_include_vat = TRUE
        """
    )
    op.drop_column("invoices", "grand_total", schema="public")
    op.drop_column("invoices", "shipping_fee", schema="public")
    op.drop_column("invoices", "net_amount_before_vat", schema="public")
    op.drop_column("invoices", "vat_amount", schema="public")
    op.drop_column("invoices", "vat_rate", schema="public")
    op.drop_column("invoices", "prices_include_vat", schema="public")
