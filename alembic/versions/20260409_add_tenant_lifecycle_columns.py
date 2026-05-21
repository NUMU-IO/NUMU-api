"""Add tenant lifecycle state machine columns.

Revision ID: d4e5f6a70b01
Revises: c1d2e3f40901
Create Date: 2026-04-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a70b01"
down_revision: str | None = "c1d2e3f40901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "lifecycle_state", sa.String(20), nullable=False, server_default="active"
        ),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("read_only_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("demo_email", sa.String(255), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("demo_started_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("trial_converted_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_tenants_lifecycle_state", "tenants", ["lifecycle_state"], schema="public"
    )
    op.create_index("ix_tenants_expires_at", "tenants", ["expires_at"], schema="public")
    op.create_index("ix_tenants_delete_at", "tenants", ["delete_at"], schema="public")


def downgrade() -> None:
    op.drop_index("ix_tenants_delete_at", table_name="tenants", schema="public")
    op.drop_index("ix_tenants_expires_at", table_name="tenants", schema="public")
    op.drop_index("ix_tenants_lifecycle_state", table_name="tenants", schema="public")
    op.drop_column("tenants", "trial_converted_at", schema="public")
    op.drop_column("tenants", "trial_started_at", schema="public")
    op.drop_column("tenants", "demo_started_at", schema="public")
    op.drop_column("tenants", "demo_email", schema="public")
    op.drop_column("tenants", "delete_at", schema="public")
    op.drop_column("tenants", "read_only_at", schema="public")
    op.drop_column("tenants", "expires_at", schema="public")
    op.drop_column("tenants", "lifecycle_state", schema="public")
