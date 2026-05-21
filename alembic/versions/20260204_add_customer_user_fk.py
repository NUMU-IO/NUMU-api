"""Add foreign key constraint on customers.user_id -> users.id.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-02-04
"""

from alembic import op

# revision identifiers
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_customers_user_id_users",
        "customers",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema="public",
        referent_schema="public",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_customers_user_id_users",
        "customers",
        type_="foreignkey",
        schema="public",
    )
