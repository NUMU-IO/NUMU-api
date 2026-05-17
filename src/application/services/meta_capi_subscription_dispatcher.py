"""Wave 4 Phase 23 — Meta CAPI dispatcher for subscription / recurring revenue.

**Gate:** NUMU has no subscriptions product as of 2026-05-17. This
module is a code-ready dispatcher that the subscriptions team can
call from their lifecycle hooks the day they ship.

Three events fire across the subscription lifecycle:

  * ``Subscribe``       — fired ONCE on initial signup. Goes to Meta
                          as the standard ``Subscribe`` event so
                          lookalikes can be built off subscribers.
  * ``Purchase``        — fired per recurring charge. Same shape as a
                          one-off purchase, with ``subscription_id`` in
                          custom_data + Advanced Matching for ongoing
                          deduplication across renewals.
  * ``CancelSubscription`` — custom event fired on cancellation. Meta
                          has no native event for this; we send it as
                          a custom event so merchants can build "lost
                          subscriber" remarketing audiences.

When subscriptions ship, the subscription lifecycle handler imports
``enqueue_meta_capi_subscribe`` / ``enqueue_meta_capi_recurring_purchase``
/ ``enqueue_meta_capi_cancel_subscription`` and calls them at the
appropriate points. No changes to the existing Meta CAPI infrastructure
needed — these helpers reuse the existing ``meta_capi_send_event`` task
and the pixel resolver fan-out.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger

logger = get_logger(__name__)


async def _resolve_store_and_pixels(db: AsyncSession, store_id: UUID):
    """Look up store + capi-enabled pixels for the fan-out.

    Returns ``(store, pixels)`` or ``(None, [])`` if CAPI isn't
    configured for this store. Caller short-circuits on the second case.
    """
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.repositories.store_repository import StoreRepository

    sr = StoreRepository(db)
    store = await sr.get_by_id(store_id)
    if store is None:
        return None, []
    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
    pixels = resolve_pixels(meta_cfg, mode="capi")
    return store, pixels


def _subscription_user_data(subscription: Any) -> dict[str, Any]:
    """Build user_data from the subscription's customer profile.

    Subscriptions persist customer context in their own object, so we
    don't need to query the orders table. ``customer`` field is
    expected to expose the standard NUMU customer attributes.
    """
    customer = getattr(subscription, "customer", None)
    if customer is None:
        return {}
    return {
        "email": getattr(customer, "email", None),
        "phone": getattr(customer, "phone", None),
        "first_name": getattr(customer, "first_name", None),
        "last_name": getattr(customer, "last_name", None),
        "customer_id": str(getattr(customer, "id", "")) or None,
    }


def _subscription_custom_data(subscription: Any, *, event_kind: str) -> dict[str, Any]:
    """Build custom_data for a subscription event."""
    base = {
        "subscription_id": str(getattr(subscription, "id", "")),
        "subscription_event": event_kind,
        "value": (getattr(subscription, "amount_cents", 0) or 0) / 100,
        "currency": getattr(subscription, "currency", "EGP"),
        "plan_name": getattr(subscription, "plan_name", None),
        "billing_interval": getattr(subscription, "billing_interval", None),
    }
    return {k: v for k, v in base.items() if v is not None}


async def enqueue_meta_capi_subscribe(db: AsyncSession, subscription: Any) -> None:
    """Fire Meta ``Subscribe`` on initial subscription signup."""
    store, pixels = await _resolve_store_and_pixels(db, subscription.store_id)
    if store is None or not pixels:
        return

    from src.infrastructure.messaging.tasks.meta_capi import meta_capi_send_event

    event_time = int(datetime.now(UTC).timestamp())
    user_data = _subscription_user_data(subscription)
    custom_data = _subscription_custom_data(subscription, event_kind="initial")
    event_id = f"subscribe-{subscription.id}"

    for pixel in pixels:
        meta_capi_send_event.delay(
            store_id=str(subscription.store_id),
            pixel_id=pixel.pixel_id,
            event_name="Subscribe",
            event_id=event_id,
            event_time=event_time,
            event_source_url=None,
            user_data=user_data,
            custom_data=custom_data,
            action_source="website",
        )
    logger.info(
        "meta_capi_subscribe_enqueued",
        extra={"subscription_id": str(subscription.id), "pixel_count": len(pixels)},
    )


async def enqueue_meta_capi_recurring_purchase(
    db: AsyncSession, subscription: Any, *, charge_id: str
) -> None:
    """Fire ``Purchase`` for each recurring billing event.

    ``event_id = f"sub-charge-{charge_id}"`` so renewals dedupe per
    charge — distinct from the initial Subscribe event and from any
    other Purchase events.
    """
    store, pixels = await _resolve_store_and_pixels(db, subscription.store_id)
    if store is None or not pixels:
        return

    from src.infrastructure.messaging.tasks.meta_capi import meta_capi_send_event

    event_time = int(datetime.now(UTC).timestamp())
    user_data = _subscription_user_data(subscription)
    custom_data = _subscription_custom_data(subscription, event_kind="recurring")
    custom_data["charge_id"] = charge_id
    event_id = f"sub-charge-{charge_id}"

    for pixel in pixels:
        meta_capi_send_event.delay(
            store_id=str(subscription.store_id),
            pixel_id=pixel.pixel_id,
            event_name="Purchase",
            event_id=event_id,
            event_time=event_time,
            event_source_url=None,
            user_data=user_data,
            custom_data=custom_data,
            action_source="system_generated",
        )


async def enqueue_meta_capi_cancel_subscription(
    db: AsyncSession, subscription: Any, *, reason: str | None = None
) -> None:
    """Fire ``CancelSubscription`` custom event on cancellation."""
    store, pixels = await _resolve_store_and_pixels(db, subscription.store_id)
    if store is None or not pixels:
        return

    from src.infrastructure.messaging.tasks.meta_capi import meta_capi_send_event

    event_time = int(datetime.now(UTC).timestamp())
    user_data = _subscription_user_data(subscription)
    custom_data = _subscription_custom_data(subscription, event_kind="cancel")
    if reason:
        custom_data["cancel_reason"] = reason
    event_id = f"cancel-{subscription.id}"

    for pixel in pixels:
        meta_capi_send_event.delay(
            store_id=str(subscription.store_id),
            pixel_id=pixel.pixel_id,
            event_name="CancelSubscription",
            event_id=event_id,
            event_time=event_time,
            event_source_url=None,
            user_data=user_data,
            custom_data=custom_data,
            action_source="system_generated",
        )
