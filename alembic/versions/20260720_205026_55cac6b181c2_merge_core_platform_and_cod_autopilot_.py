"""merge core-platform and cod-autopilot heads

Revision ID: 55cac6b181c2
Revises: demo_name_intent_20260719, installation_preview_20260720
Create Date: 2026-07-20 20:50:26.258740

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "55cac6b181c2"
down_revision: str | None = (
    "demo_name_intent_20260719",
    "installation_preview_20260720",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
