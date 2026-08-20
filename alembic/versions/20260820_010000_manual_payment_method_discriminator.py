"""Generalize instapay_intents into a manual-payment intent table.

Vodafone Cash uses exactly the same out-of-band shape as InstaPay
(publish a destination, customer pushes funds, uploads a screenshot,
merchant/OCR verifies). Rather than clone the table, this adds a
``method`` discriminator and renames the InstaPay-specific
``display_ipa`` column to the rail-neutral ``display_destination``.

The table itself keeps its historical name — renaming it would force a
coordinated deploy for zero functional gain.

Revision ID: manual_pay_method_20260820
Revises: meta_outbox_lifecycle_20260818
Create Date: 2026-08-20
"""

from alembic import op

revision = "manual_pay_method_20260820"
down_revision = "meta_outbox_lifecycle_20260818"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent by construction: prod has had columns hand-created
    # before (see docs/alembic notes), and a re-run of this revision
    # must not blow up on an already-migrated table.
    op.execute(
        """
        ALTER TABLE public.instapay_intents
            ADD COLUMN IF NOT EXISTS method VARCHAR(24)
            NOT NULL DEFAULT 'instapay'
        """
    )

    # RENAME COLUMN has no IF EXISTS form, so guard on the catalog.
    # Every existing row is InstaPay, so the DEFAULT above is also the
    # correct backfill — no UPDATE pass needed.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'instapay_intents'
                  AND column_name = 'display_ipa'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'instapay_intents'
                  AND column_name = 'display_destination'
            ) THEN
                ALTER TABLE public.instapay_intents
                    RENAME COLUMN display_ipa TO display_destination;
            END IF;
        END $$;
        """
    )

    # The sweeper scans (status, expires_at) across all methods, so no
    # index on ``method`` alone. The merchant "pending review" list
    # filters orders, not intents. This one supports the per-store,
    # per-method lookups the settings + reporting paths do.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_instapay_intents_store_method
            ON public.instapay_intents (store_id, method)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_instapay_intents_store_method")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'instapay_intents'
                  AND column_name = 'display_destination'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'instapay_intents'
                  AND column_name = 'display_ipa'
            ) THEN
                ALTER TABLE public.instapay_intents
                    RENAME COLUMN display_destination TO display_ipa;
            END IF;
        END $$;
        """
    )
    # Vodafone Cash intents would be orphaned by a downgrade — drop
    # them rather than leave rows the old code would render as
    # InstaPay (wrong IPA, wrong instructions, wrong QR).
    op.execute("DELETE FROM public.instapay_intents WHERE method <> 'instapay'")
    op.execute("ALTER TABLE public.instapay_intents DROP COLUMN IF EXISTS method")
