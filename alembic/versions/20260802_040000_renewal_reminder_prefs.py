"""Per-tenant renewal reminder preferences (merchant-controlled).

The PLATFORM default window lives in billing_lifecycle_settings
(admin-controlled). These two columns let a merchant tune their own
renewal reminders from the hub Billing page:

- ``renewal_reminder_days`` — NULL = use the platform default window;
  1-30 = remind me this many days before my renewal.
- ``renewal_reminder_optout`` — TRUE = no renewal-reminder emails for
  this tenant (dunning emails still fire; those are operational, not
  a courtesy).

Revision ID: sub_reminder_20260802
Revises: sub_lifecycle_20260802
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "sub_reminder_20260802"
down_revision: str | Sequence[str] | None = "sub_lifecycle_20260802"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("renewal_reminder_days", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column(
            "renewal_reminder_optout",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("tenants", "renewal_reminder_optout", schema="public")
    op.drop_column("tenants", "renewal_reminder_days", schema="public")
