"""Service layer for paid-theme purchases.

The marketplace install endpoint already gates access for paid themes
(see MarketplaceService.install_theme). This service owns:

  * Creating Stripe payment intents and pending purchase rows.
  * Reconciling Stripe webhook events into purchase status updates.
  * Issuing refunds (full or partial) and revoking install rights.

Idempotency story:
  - Stripe identifies every payment by `payment_intent.id` — that's our
    natural key. We `UNIQUE(stripe_payment_intent_id)` so duplicate
    webhook deliveries silently no-op (the upsert finds the existing
    row).
  - Refunds are keyed on `refund.id` in metadata; partial refunds keep
    accumulating into `refunded_amount_cents`.

Failure semantics:
  - A failed Stripe charge sets `status=failed`. The buyer can retry
    via /checkout-session (a new Stripe intent + new purchase row).
  - An admin can issue a refund through `/purchases/{id}/refund`. We
    do NOT auto-uninstall — keeping a refunded theme alive on existing
    storefronts beats abruptly breaking a live shop. New installs and
    reactivations of the same theme by that user are blocked instead.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.core.entities.marketplace_theme import (
    MarketplacePurchaseStatus,
    MarketplaceThemePurchase,
)
from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import IPaymentService
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

logger = logging.getLogger(__name__)


class ThemePurchaseService:
    """Owns the paid-theme purchase + refund flow."""

    def __init__(
        self,
        *,
        marketplace_repo: MarketplaceRepository,
        payment_service: IPaymentService,
    ) -> None:
        self._marketplace = marketplace_repo
        self._payments = payment_service

    # ── Checkout ──────────────────────────────────────────────────────────────

    async def create_checkout_session(
        self,
        *,
        user_id: UUID,
        marketplace_theme_id: UUID,
        customer_email: str | None = None,
    ) -> dict[str, Any]:
        """Create a Stripe payment intent + pending purchase row.

        Returns the payment intent's `client_secret` so the client-side
        Stripe.js form can confirm the charge directly without our
        backend ever seeing card details. The pending purchase row is
        promoted to `succeeded` by the webhook handler when Stripe
        confirms the charge.
        """
        theme = await self._marketplace.get_theme_by_id(marketplace_theme_id)
        if theme is None:
            raise ValueError("Marketplace theme not found")
        if theme.price_cents <= 0:
            raise ValueError(
                "This is a free theme — no purchase needed. Use /install directly."
            )

        # Don't let a buyer accumulate multiple succeeded purchases for the
        # same theme; the second would be free money for us. Pending rows
        # ARE allowed (a stale Stripe intent the buyer abandoned).
        if await self._marketplace.has_active_purchase(user_id, marketplace_theme_id):
            raise ValueError("You already own this theme — install it directly.")

        try:
            intent = await self._payments.create_payment_intent(
                amount=theme.price_cents,
                currency=theme.currency,
                customer_email=customer_email,
                metadata={
                    "kind": "marketplace_theme",
                    "marketplace_theme_id": str(marketplace_theme_id),
                    "user_id": str(user_id),
                },
            )
        except PaymentError as e:
            raise ValueError(f"Failed to create payment intent: {e}")

        purchase = await self._marketplace.create_purchase(
            user_id=user_id,
            marketplace_theme_id=marketplace_theme_id,
            amount_cents=theme.price_cents,
            currency=theme.currency,
            stripe_payment_intent_id=intent.id,
            purchase_metadata={"theme_slug": theme.slug},
        )

        return {
            "purchase_id": str(purchase.id),
            "payment_intent_id": intent.id,
            "client_secret": intent.client_secret,
            "amount_cents": theme.price_cents,
            "currency": theme.currency,
        }

    # ── Webhook reconciliation ────────────────────────────────────────────────

    async def handle_stripe_event(self, event: dict[str, Any]) -> None:
        """Reconcile a Stripe webhook event into our purchase row.

        Idempotent: the unique constraint on `stripe_payment_intent_id`
        means duplicate event deliveries hit the same row. Unknown
        event types are logged and ignored — we only care about
        payment-intent and refund lifecycle events.
        """
        event_type = event.get("type")
        data = event.get("data", {}).get("object", {})

        # Stripe sends a handful of types we care about:
        #   payment_intent.succeeded — promote pending → succeeded
        #   payment_intent.payment_failed — pending → failed
        #   charge.refunded — record refund (partial or full)
        if event_type == "payment_intent.succeeded":
            await self._on_intent_succeeded(data)
        elif event_type == "payment_intent.payment_failed":
            await self._on_intent_failed(data)
        elif event_type == "charge.refunded":
            await self._on_charge_refunded(data)
        else:
            logger.debug("Ignoring Stripe event type: %s", event_type)

    async def _on_intent_succeeded(self, intent: dict[str, Any]) -> None:
        intent_id = intent.get("id")
        if not intent_id:
            return
        purchase = await self._marketplace.get_purchase_by_intent(intent_id)
        if purchase is None:
            # Intent was never created via our /checkout endpoint (stray
            # event from a different application). Ignore.
            logger.warning("No purchase row for intent %s; ignoring", intent_id)
            return
        # Latest charge id surfaces under `latest_charge` on the intent.
        # We persist it because refund operations target charges, not
        # intents (Stripe's API).
        charge_id = intent.get("latest_charge")
        await self._marketplace.update_purchase(
            purchase.id,
            status=MarketplacePurchaseStatus.SUCCEEDED,
            stripe_charge_id=charge_id,
        )

    async def _on_intent_failed(self, intent: dict[str, Any]) -> None:
        intent_id = intent.get("id")
        if not intent_id:
            return
        purchase = await self._marketplace.get_purchase_by_intent(intent_id)
        if purchase is None:
            return
        await self._marketplace.update_purchase(
            purchase.id,
            status=MarketplacePurchaseStatus.FAILED,
        )

    async def _on_charge_refunded(self, charge: dict[str, Any]) -> None:
        # `payment_intent` is included on the charge object.
        intent_id = charge.get("payment_intent")
        if not intent_id:
            return
        purchase = await self._marketplace.get_purchase_by_intent(intent_id)
        if purchase is None:
            return

        amount_refunded = charge.get("amount_refunded", 0)
        if amount_refunded >= purchase.amount_cents:
            new_status = MarketplacePurchaseStatus.REFUNDED
        else:
            new_status = MarketplacePurchaseStatus.PARTIALLY_REFUNDED
        await self._marketplace.update_purchase(
            purchase.id,
            status=new_status,
            refunded_amount_cents=amount_refunded,
        )

    # ── Manual refund (admin / buyer initiated) ───────────────────────────────

    async def refund_purchase(
        self,
        *,
        purchase_id: UUID,
        amount_cents: int | None = None,
        reason: str | None = None,
    ) -> MarketplaceThemePurchase:
        """Issue a Stripe refund and update the purchase row.

        `amount_cents=None` → refund whatever's not yet been refunded.
        Partial refunds set status to PARTIALLY_REFUNDED. Calling refund
        again on a partially-refunded purchase tops up the refund amount.
        """
        purchase = await self._marketplace.get_purchase_by_id(purchase_id)
        if purchase is None:
            raise ValueError("Purchase not found")
        if purchase.status not in (
            MarketplacePurchaseStatus.SUCCEEDED,
            MarketplacePurchaseStatus.PARTIALLY_REFUNDED,
        ):
            raise ValueError(
                f"Cannot refund a purchase in status {purchase.status.value}"
            )

        remaining = purchase.amount_cents - purchase.refunded_amount_cents
        if remaining <= 0:
            raise ValueError("Purchase has already been fully refunded")

        refund_amount = amount_cents if amount_cents is not None else remaining
        if refund_amount <= 0 or refund_amount > remaining:
            raise ValueError(f"refund amount must be between 1 and {remaining} cents")

        if not purchase.stripe_payment_intent_id:
            raise ValueError("Cannot refund a purchase without a Stripe payment intent")

        refund_result = await self._payments.refund_payment(
            payment_id=purchase.stripe_payment_intent_id,
            amount=refund_amount,
        )
        if not refund_result.success:
            raise ValueError(f"Stripe refund failed: {refund_result.error_message}")

        new_total_refunded = purchase.refunded_amount_cents + refund_amount
        new_status = (
            MarketplacePurchaseStatus.REFUNDED
            if new_total_refunded >= purchase.amount_cents
            else MarketplacePurchaseStatus.PARTIALLY_REFUNDED
        )
        await self._marketplace.update_purchase(
            purchase_id,
            status=new_status,
            refunded_amount_cents=new_total_refunded,
            refund_reason=reason,
        )
        # Re-fetch so the entity returned reflects the updated values.
        updated = await self._marketplace.get_purchase_by_id(purchase_id)
        # `update_purchase` is non-empty by construction so the row exists.
        assert updated is not None
        return updated

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_user_purchases(
        self, user_id: UUID
    ) -> list[MarketplaceThemePurchase]:
        return await self._marketplace.list_purchases_by_user(user_id)
