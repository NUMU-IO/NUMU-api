"""Add customer_confirmation_status + customer_confirmed_at to orders.

Powers the WhatsApp interactive order-confirmation flow (backend-031).
When the store opts into ``require_order_confirmation``, OrderCreatedEvent
sends a QUICK_REPLY template; the customer's tap arrives as an inbound
webhook that flips ``customer_confirmation_status`` to ``confirmed`` +
stamps ``customer_confirmed_at``.

Schema is intentionally a plain VARCHAR(20) rather than a PG ENUM —
forward-compat for new states (``declined``, ``no_response``) without
running an ENUM ALTER on a hot table. The state machine lives in the
application layer; the column is just storage.

Revision ID: order_cust_confirm_20260730
Revises: wa_notif_defaults_20260729
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "order_cust_confirm_20260730"
down_revision: str | None = "wa_notif_defaults_20260729"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "customer_confirmation_status",
            sa.String(length=20),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column(
            "customer_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="public",
    )
    # Index supports the "pending customer confirmation" filter the
    # merchant-hub orders list adds. Partial index keeps it small —
    # the vast majority of rows will have NULL status (stores that
    # haven't opted in) and we never query for those.
    op.create_index(
        "ix_orders_pending_customer_confirmation",
        "orders",
        ["store_id", "customer_confirmation_status"],
        schema="public",
        postgresql_where=sa.text("customer_confirmation_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_orders_pending_customer_confirmation",
        table_name="orders",
        schema="public",
    )
    op.drop_column("orders", "customer_confirmed_at", schema="public")
    op.drop_column("orders", "customer_confirmation_status", schema="public")
