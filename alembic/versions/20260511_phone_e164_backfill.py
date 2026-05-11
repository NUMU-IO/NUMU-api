"""Backfill legacy phone columns to canonical E.164.

Part of the Part 1 (international phone system) rollout. Once the
``PhoneField`` Pydantic type lands at the API boundary, every newly-
written phone value will be canonical E.164 (``+201001234567``). This
migration drags the existing rows forward in one pass:

- Any value matching the legacy Egyptian shape ``^01\\d{9}$`` is rewritten
  to ``+20<digits-without-leading-zero>``.
- Any value already starting with ``+`` is left alone (we trust it was
  set by code that knew what it was doing).
- Anything else stays verbatim. The new ``phone_normalised_at`` column
  is set to ``NOW()`` only on rows we *did* normalise (or already-canonical
  rows) so we can re-run a manual-fix pass later by filtering on
  ``phone_normalised_at IS NULL AND phone IS NOT NULL``.

The columns touched (all defined as ``String(20)``):

- ``public.users.phone``
- ``public.customers.phone``
- ``public.customer_addresses.phone``
- ``public.stores.contact_phone``

We intentionally skip the ``orders`` table — checkout writes go through
the new field already and historical order phones are point-in-time
audit data, not something we re-dial.

Revision ID: phone_e164_backfill_20260511
Revises: merge_bogo_phase6_20260510
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "phone_e164_backfill_20260511"
down_revision: str | None = "merge_bogo_phase6_20260510"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, phone_column) — schema is ``public`` for every tenant model
# in this codebase (shared-schema + RLS).
_TARGETS: tuple[tuple[str, str], ...] = (
    ("users", "phone"),
    ("customers", "phone"),
    ("customer_addresses", "phone"),
    ("stores", "contact_phone"),
)


def upgrade() -> None:
    for table, _ in _TARGETS:
        op.add_column(
            table,
            sa.Column(
                "phone_normalised_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            schema="public",
        )

    # Two-phase backfill per target table, inside a single Alembic
    # transaction. ``RAISE NOTICE`` on rows we couldn't parse so the
    # operator running the migration sees the manual-fix bucket size.
    for table, column in _TARGETS:
        op.execute(
            f"""
            UPDATE public.{table}
               SET {column} = '+20' || substring({column} from 2),
                   phone_normalised_at = NOW()
             WHERE {column} ~ '^01[0-9]{{9}}$';
            """
        )
        op.execute(
            f"""
            UPDATE public.{table}
               SET phone_normalised_at = NOW()
             WHERE {column} LIKE '+%'
               AND phone_normalised_at IS NULL;
            """
        )
        op.execute(
            f"""
            DO $$
            DECLARE
                unparsed integer;
            BEGIN
                SELECT count(*) INTO unparsed
                  FROM public.{table}
                 WHERE {column} IS NOT NULL
                   AND {column} <> ''
                   AND phone_normalised_at IS NULL;
                IF unparsed > 0 THEN
                    RAISE NOTICE
                        'phone_e164_backfill: % rows in public.{table}.{column} '
                        'left un-normalised (need manual review)',
                        unparsed;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    # The ``+20…`` rewrite isn't reversible in a way that preserves user
    # intent (we'd have to assume every ``+20…`` row was originally an
    # ``01…`` row, which is wrong post-PR-2 once SA/AE customers start
    # registering). Drop the tracking column only; phone digits stay
    # in their canonical form. If you need a true rollback, do it via
    # a point-in-time DB restore — not Alembic.
    for table, _ in _TARGETS:
        op.drop_column(table, "phone_normalised_at", schema="public")
