"""merge whatsapp and omnichannel_inbox heads

Revision ID: f1a2b3c40418
Revises: wa0413b2c3d4, omnichannel_inbox_v1
Create Date: 2026-04-18 00:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c40418"
down_revision: str | None = ("wa0413b2c3d4", "omnichannel_inbox_v1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
