"""merge seo_fields and trust_network heads

Revision ID: 0c1c1d87f317
Revises: ee7788990033, 851effd425a2
Create Date: 2026-04-01 15:19:21.777049

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0c1c1d87f317"
down_revision: str | None = ("ee7788990033", "851effd425a2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
