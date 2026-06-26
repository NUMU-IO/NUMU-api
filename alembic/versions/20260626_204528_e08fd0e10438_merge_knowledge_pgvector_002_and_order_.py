"""merge knowledge_pgvector_002 and order_applied_promos heads

Revision ID: e08fd0e10438
Revises: order_applied_promos_20260615, knowledge_pgvector_002_20260626
Create Date: 2026-06-26 20:45:28.460042

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e08fd0e10438"
down_revision: str | None = (
    "order_applied_promos_20260615",
    "knowledge_pgvector_002_20260626",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
