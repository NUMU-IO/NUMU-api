"""Add tenants.renewal_warning_sent_at (pre-expiry warning dedup).

One nullable timestamp shared by both warning kinds (a tenant is either
on trial or on a paid plan, never both): the warning sweep stamps it
when it emails, and re-arms automatically once the anchor
(``expires_at`` for trials, ``next_renewal_at`` for paid) moves to a
new period — the dedup rule is "warned inside THIS period's window",
not "warned ever".

Revision ID: sub_lifecycle_20260802
Revises: sub_instapay_20260802
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "sub_lifecycle_20260802"
down_revision: str | Sequence[str] | None = "sub_instapay_20260802"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("renewal_warning_sent_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("tenants", "renewal_warning_sent_at", schema="public")
