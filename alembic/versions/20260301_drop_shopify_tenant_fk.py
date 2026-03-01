"""Drop tenant_id FK on all Shopify tables and make nullable.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None

# Tables that still need FK dropped (shopify_installations already done)
_TABLES = [
    "risk_assessments",
    "payment_transactions",
    "automation_rules",
    "automation_logs",
    "shopify_app_settings",
]


def upgrade() -> None:
    for table in _TABLES:
        fk_name = f"{table}_tenant_id_fkey"
        op.drop_constraint(fk_name, table, schema="public", type_="foreignkey")
        op.alter_column(
            table,
            "tenant_id",
            existing_type=sa.UUID(),
            nullable=True,
            schema="public",
        )


def downgrade() -> None:
    for table in _TABLES:
        fk_name = f"{table}_tenant_id_fkey"
        op.alter_column(
            table,
            "tenant_id",
            existing_type=sa.UUID(),
            nullable=False,
            schema="public",
        )
        op.create_foreign_key(
            fk_name,
            table,
            "tenants",
            ["tenant_id"],
            ["id"],
            source_schema="public",
            referent_schema="public",
            ondelete="CASCADE",
        )
