"""merge recovery + dev heads

Revision ID: b404e12b9871
Revises: trust_network_consent_20260509, 3717ff7cf723, phone_e164_backfill_20260511
Create Date: 2026-05-11 21:07:40.483332

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b404e12b9871'
down_revision: Union[str, None] = ('trust_network_consent_20260509', '3717ff7cf723', 'phone_e164_backfill_20260511')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
