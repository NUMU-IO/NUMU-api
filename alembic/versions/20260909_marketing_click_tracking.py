"""Click tracking for marketing outreach.

`marketing_outreach` recorded that we sent something and what it said, but
never whether it landed. "Did the referral campaign work" was unanswerable
beyond "40 sent, 0 failed", which measures our own outbox rather than the
merchant's interest.

Tracking runs through our own redirect rather than the provider's. Resend can
rewrite links for click tracking, but it rewrites them to a Resend-owned host,
which is exactly the link/domain mismatch its own deliverability report already
flags against this account — buying a metric by spending reputation. A token
resolved on numueg.app keeps every link on the sending domain.

`links` holds the destinations the tracked URLs resolve to. Keeping them on the
row rather than in the redirect's query string is what stops the endpoint being
an open redirect: it can only ever send someone to a URL we wrote down when we
sent the email.

Revision ID: marketing_click_20260909
Revises: marketing_referrals_20260909
"""

from alembic import op

revision = "marketing_click_20260909"
down_revision = "marketing_referrals_20260909"
branch_labels = None
depends_on = None

_TABLE = "marketing_outreach"
_SCHEMA = "public"


def upgrade() -> None:
    # IF NOT EXISTS throughout: prod has had columns hand-added before a
    # migration caught up, and a re-run must not be the thing that breaks a
    # deploy.
    op.execute(
        f"ALTER TABLE {_SCHEMA}.{_TABLE} "
        "ADD COLUMN IF NOT EXISTS click_token VARCHAR(64)"
    )
    op.execute(
        f"ALTER TABLE {_SCHEMA}.{_TABLE} "
        "ADD COLUMN IF NOT EXISTS links JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        f"ALTER TABLE {_SCHEMA}.{_TABLE} "
        "ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMPTZ"
    )
    op.execute(
        f"ALTER TABLE {_SCHEMA}.{_TABLE} "
        "ADD COLUMN IF NOT EXISTS click_count INTEGER NOT NULL DEFAULT 0"
    )
    # Unique, not just indexed: the token IS the lookup key for a public
    # endpoint, and two rows sharing one would attribute a click to whichever
    # the planner returned first. Partial, because every row sent before this
    # migration — and every WhatsApp row, which has no tracked links — has
    # NULL and several NULLs must not collide.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{_TABLE}_click_token "
        f"ON {_SCHEMA}.{_TABLE} (click_token) WHERE click_token IS NOT NULL"
    )
    # The reporting query is "clicks for this campaign", i.e. filter on
    # template_key and count non-null clicked_at.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{_TABLE}_template_clicked "
        f"ON {_SCHEMA}.{_TABLE} (template_key, clicked_at)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_SCHEMA}.ix_{_TABLE}_template_clicked")
    op.execute(f"DROP INDEX IF EXISTS {_SCHEMA}.ux_{_TABLE}_click_token")
    for column in ("click_count", "clicked_at", "links", "click_token"):
        op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} DROP COLUMN IF EXISTS {column}")
