"""merge recovery + dev heads

Revision ID: b404e12b9871
Revises: trust_network_consent_20260509, 3717ff7cf723, phone_e164_backfill_20260511
Create Date: 2026-05-11 21:07:40.483332

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "b404e12b9871"
down_revision: str | None = (
    "trust_network_consent_20260509",
    "3717ff7cf723",
    "phone_e164_backfill_20260511",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
