"""Add PAYMENT_FAILED (uppercase) to orderstatus enum.

Revision ID: payment_failed_upper_20260909
Revises: agent_proposal_store_20260908
Create Date: 2026-09-09

The same case bug that ``20260426_pending_deposit_uppercase_fix.py`` and
``20260426_returned_uppercase_fix.py`` already fixed for their own members,
left unfixed for this one.

``20260220_add_payment_failed_to_orderstatus.py`` added ``'payment_failed'``
(lowercase) to the ``orderstatus`` enum. ``OrderModel.status`` does not
configure ``values_callable``, so SQLAlchemy serialises the member NAME —
``'PAYMENT_FAILED'`` — which is not a label on the type. Every query that
filters on that status therefore dies with:

    invalid input value for enum orderstatus: "PAYMENT_FAILED"

That is not hypothetical: ``GET /admin/orders/?status=payment_failed`` 500s,
and so does any count of failed payments. The April fixes were applied
member-by-member as each one was hit in production, and this member was
simply never hit until the admin overview started counting it.

Postgres cannot drop enum labels, so the uppercase form is added additively
and the unused lowercase one stays. Any row still carrying the lowercase
label is moved over, so the two spellings do not both circulate.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "payment_failed_upper_20260909"
down_revision: str | None = "agent_proposal_store_20260908"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE cannot be used in the same transaction that adds
    # it, so the label goes in on its own connection first and the row
    # migration below runs afterwards.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'PAYMENT_FAILED'"
        )

    # Anything written while only the lowercase label existed. Usually zero
    # rows — the status was unreachable through the API for the same reason
    # this migration exists — but a webhook or a direct write could have set
    # it, and a split spelling would hide those orders from every filter.
    op.execute(
        "UPDATE public.orders SET status = 'PAYMENT_FAILED' "
        "WHERE status::text = 'payment_failed'"
    )


def downgrade() -> None:
    # PostgreSQL cannot remove an enum label. Leaving the uppercase form in
    # place is harmless; the rows stay readable either way.
    pass
