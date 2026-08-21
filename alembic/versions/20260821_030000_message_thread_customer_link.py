"""Link a message thread to a customer record.

Meta never exposes a phone number for a Messenger/Instagram sender, so a
conversation can only be tied to a customer deliberately — an agent
confirms who they are talking to and links the thread. Fuzzy-matching
names would silently merge identities and poison Trust Network data, so
the link is always an explicit, reversible action.

Idempotent: ADD COLUMN IF NOT EXISTS, safe to re-run.

Revision ID: thread_customer_20260821
Revises: chconn_linked_page_20260821
Create Date: 2026-08-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "thread_customer_20260821"
down_revision = "chconn_linked_page_20260821"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.message_threads
        ADD COLUMN IF NOT EXISTS customer_id UUID
            REFERENCES public.customers(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_threads_customer
        ON public.message_threads (customer_id)
        WHERE customer_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_message_threads_customer")
    op.execute("ALTER TABLE public.message_threads DROP COLUMN IF EXISTS customer_id")
