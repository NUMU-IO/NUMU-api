"""Backfill store.settings.whatsapp_notifications for existing stores.

The WhatsApp order-lifecycle handlers read the merchant-side toggle from
``store.settings.whatsapp_notifications.{order_confirmation,
payment_received, shipping_update, delivery_confirmation}``. New stores
get the dict seeded at creation time (create_store.py, this PR), but
stores that already exist were created with an empty ``settings`` dict
— the handler falls through to ``dict.get(key, True)`` which works by
accident but masks intent.

This migration writes the canonical default object onto every store row
that doesn't already have the key. Idempotent — re-runnable. Won't
overwrite values a merchant has explicitly set (the WHERE clause
restricts to rows where the key is JSON-null).

Revision ID: wa_notif_defaults_20260729
Revises: merge_meta_wa_heads_20260525
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "wa_notif_defaults_20260729"
down_revision: str | None = "merge_meta_wa_heads_20260525"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Canonical default object. abandoned_cart is deliberately False — the
# scheduled-send dispatcher (US3) hasn't shipped yet, so the toggle
# would be a no-op if rendered ON. US3 lands the dispatcher AND flips
# the default in a coordinated migration. marketing is intentionally
# absent (opt-in gated, separate governance surface).
_DEFAULT_TOGGLES = (
    '{"order_confirmation": true,'
    ' "payment_received": true,'
    ' "shipping_update": true,'
    ' "delivery_confirmation": true,'
    ' "abandoned_cart": false}'
)


def upgrade() -> None:
    # NULL coalesce ensures stores with NULL settings get a fresh
    # object too (jsonb_set on NULL returns NULL otherwise).
    op.execute(
        f"""
        UPDATE public.stores
           SET settings = jsonb_set(
                   COALESCE(settings, '{{}}'::jsonb),
                   '{{whatsapp_notifications}}',
                   '{_DEFAULT_TOGGLES}'::jsonb
               )
         WHERE settings IS NULL
            OR settings -> 'whatsapp_notifications' IS NULL
        """
    )


def downgrade() -> None:
    # Only strip the key from rows where the entire object matches the
    # default we wrote — preserves any toggles the merchant has flipped
    # since the upgrade ran.
    op.execute(
        f"""
        UPDATE public.stores
           SET settings = settings - 'whatsapp_notifications'
         WHERE settings -> 'whatsapp_notifications' = '{_DEFAULT_TOGGLES}'::jsonb
        """
    )
