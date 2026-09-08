"""Record what each agent turn cost.

The provider returns prompt and completion token counts on every completion
and the turn row dropped them, so "what does this store cost us" had no answer
but a guess. Summed across the turn, since one turn is up to five model calls.

Revision ID: agent_turn_tokens_20260908
Revises: agent_proposal_store_20260908
"""

from alembic import op

revision = "agent_turn_tokens_20260908"
down_revision = "agent_proposal_store_20260908"
branch_labels = None
depends_on = None

_TABLE = "agent_turns"


def upgrade() -> None:
    # IF NOT EXISTS: prod has carried hand-created objects before, and a
    # migration that cannot be re-run is one that blocks a deploy.
    for column in ("prompt_tokens", "completion_tokens"):
        op.execute(
            f"ALTER TABLE public.{_TABLE} ADD COLUMN IF NOT EXISTS {column} INTEGER"
        )


def downgrade() -> None:
    for column in ("prompt_tokens", "completion_tokens"):
        op.execute(f"ALTER TABLE public.{_TABLE} DROP COLUMN IF EXISTS {column}")
