"""Bind an agent action proposal to the store it was built against.

Confirm takes its store from the URL, so without this a tenant with two stores
could propose a change on store A and confirm it at ``/stores/B/agent/confirm``:
the params were computed against A's draft and the write landed on B.

Nullable on purpose. Rows created before this column have no store to check
against and are refused at apply time rather than trusted, so there is nothing
to backfill — and production has no proposals at all today.

Revision ID: agent_proposal_store_20260908
Revises: intake_completion_20260902
"""

from alembic import op

revision = "agent_proposal_store_20260908"
down_revision = "intake_completion_20260902"
branch_labels = None
depends_on = None

_TABLE = "agent_action_proposals"
_COLUMN = "store_id"
_INDEX = "ix_agent_action_proposals_store_id"


def upgrade() -> None:
    # IF NOT EXISTS: prod has carried hand-created objects before, and a
    # migration that cannot be re-run is a migration that blocks a deploy.
    op.execute(f"ALTER TABLE public.{_TABLE} ADD COLUMN IF NOT EXISTS {_COLUMN} UUID")
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON public.{_TABLE} ({_COLUMN})")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS public.{_INDEX}")
    op.execute(f"ALTER TABLE public.{_TABLE} DROP COLUMN IF EXISTS {_COLUMN}")
