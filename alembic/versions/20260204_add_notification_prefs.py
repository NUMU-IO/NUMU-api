"""Add notification_prefs column to customers table.

Revision ID: a1b2c3d4e5f6
Revises: baaa87d27421
Create Date: 2026-02-04
"""

from alembic import op

# revision identifiers
revision = "a1b2c3d4e5f6"
down_revision = "baaa87d27421"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if column already exists (idempotent)
    col_exists = conn.exec_driver_sql("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'customers'
          AND column_name = 'notification_prefs'
    """).scalar()

    if not col_exists:
        conn.exec_driver_sql("""
            ALTER TABLE public.customers
            ADD COLUMN notification_prefs JSONB NOT NULL
            DEFAULT '{"email":{"order_confirmation":true,"shipping_update":true,"delivery_confirmation":true},"whatsapp":{"order_confirmation":true,"shipping_update":true,"delivery_confirmation":true}}'::jsonb
        """)


def downgrade() -> None:
    op.drop_column("customers", "notification_prefs", schema="public")
