"""Single source of truth for Meta CAPI Purchase fan-out across all
payment confirmation paths (Paymob, Fawry, Fawaterak, Instapay,
Kashier, COD).

Plan §5.4 — Purchase is server-authoritative: the browser-side
``fbq('track', 'Purchase', ..., { eventID: order.id })`` fire is
best-effort (script blockers, page abandons after redirect, brand-new
device with no JS context); the webhook hook here is the one Meta
optimizes ad spend against.

Dedup contract:
    event_id = str(order.id)

The storefront's ``BaseOrderConfirmationPage`` passes the same id when
it fires ``Purchase`` from the browser, so Pixel + CAPI collapse to
one event in Meta's Events Manager (~48h dedup window on the tuple
``(pixel_id, event_name, event_id)``).

All callers should:
    try:
        await enqueue_meta_capi_purchase(db, order)
    except Exception:
        log.warning("meta_capi_purchase_enqueue_failed", exc_info=True)

Failures must NEVER fail the webhook. The hourly orphan-purchase sweep
(plan §5.5) catches missed events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


def _build_user_data_from_order(order: Any) -> dict[str, Any]:
    """Extract Meta-CAPI user_data from an Order.

    The Meta CAPI client SHA-256-hashes PII downstream (see
    ``meta/hashing.py``) — we forward raw values here so the
    dispatcher stays oblivious to that contract.

    ``ip`` and ``user_agent`` come from the order metadata snapshot
    captured at checkout-create time (storefront/checkout.py) — using
    the webhook request's IP would attribute the conversion to
    Paymob/Fawry's data centre, not the customer's device. Falling
    back to None when the metadata snapshot is missing (legacy orders,
    COD-via-courier paths) is fine — Meta drops null fields server-side.
    """
    from src.infrastructure.external_services.meta.country_iso import (
        canonicalize_country,
    )

    shipping = order.shipping_address or {}
    meta = getattr(order, "metadata", None) or {}
    # Country is free-form on the address ("Egypt", "EG", "مصر"). Meta
    # only indexes the hash of the lowercase ISO-2 code, so non-canonical
    # values would silently miss — run them through the mapper and drop
    # anything unrecognized. ``country_code`` (if explicitly set) takes
    # precedence over the free-form ``country``.
    raw_country = shipping.get("country_code") or shipping.get("country")
    return {
        "email": shipping.get("email"),
        "phone": shipping.get("phone"),
        "first_name": shipping.get("first_name"),
        "last_name": shipping.get("last_name"),
        "city": shipping.get("city"),
        "country_code": canonicalize_country(raw_country),
        "zip": shipping.get("postal_code") or shipping.get("zip"),
        "customer_id": str(order.customer_id) if order.customer_id else None,
        "ip": meta.get("ip_address"),
        "user_agent": meta.get("user_agent"),
    }


def _build_custom_data_from_order(order: Any) -> dict[str, Any]:
    """Build the Meta CAPI custom_data dict for an Order.

    Forwards the order's UTM attribution (captured by feature 001 at
    checkout-create time from the storefront's ``numu_attribution``
    cookie) into the event payload so Meta's Events Manager can split
    conversions by NUMU marketing campaign instead of collapsing them
    all into ``Direct``.

    Meta accepts arbitrary keys on ``custom_data`` and surfaces them as
    filter / breakdown dimensions, so the merchant can pivot the
    Purchase report by ``numu_utm_campaign`` and see exactly which
    email/SMS campaign drove the sale — even if the user landed via a
    Meta ad first (last-touch wins per Meta's default attribution
    window, but the campaign signal is now visible alongside the ad
    signal for cross-channel reconciliation).
    """
    line_items = order.line_items or []
    contents = [
        {
            "id": str(li.get("product_id", "")),
            "quantity": int(li.get("quantity", 1)),
            "item_price": int(li.get("unit_price", 0)) / 100,
        }
        for li in line_items
        if li.get("product_id")
    ]
    data: dict[str, Any] = {
        "value": (order.total or 0) / 100,
        "currency": order.currency or "EGP",
        "content_ids": [
            str(li.get("product_id")) for li in line_items if li.get("product_id")
        ],
        "content_type": "product",
        "contents": contents,
        "num_items": sum(int(li.get("quantity", 1)) for li in line_items),
        "order_id": str(order.id),
    }

    # UTM attribution carry-through. ``utm_campaign`` carries the
    # marketing_campaigns.short_code (Crockford base32, set by the
    # trackable-link builder). ``campaign_id`` is the canonical UUID FK
    # when the short code resolved to a known campaign — Meta sees both
    # so analysts can correlate via either dimension.
    if getattr(order, "utm_source", None):
        data["numu_utm_source"] = order.utm_source
    if getattr(order, "utm_medium", None):
        data["numu_utm_medium"] = order.utm_medium
    if getattr(order, "utm_campaign", None):
        data["numu_utm_campaign"] = order.utm_campaign
        # `numu_campaign_id` is the spec-level alias; keep both for
        # discoverability in Events Manager UI.
        data["numu_campaign_id"] = order.utm_campaign
    if getattr(order, "utm_term", None):
        data["numu_utm_term"] = order.utm_term
    if getattr(order, "utm_content", None):
        data["numu_utm_content"] = order.utm_content
    if getattr(order, "campaign_id", None):
        data["numu_campaign_uuid"] = str(order.campaign_id)

    return data


async def enqueue_meta_capi_event_for_order(
    db: AsyncSession,
    order: Any,
    *,
    event_name: str = "Purchase",
    event_id: str | None = None,
) -> None:
    """Enqueue any Meta CAPI event for an order, gated on store config.

    Wave 2 Phase 12 generalization of ``enqueue_meta_capi_purchase``.
    Supports firing ``Lead`` and ``Purchase`` from the order-status
    event handler when a store has ``purchase_trigger`` /
    ``lead_trigger`` configured for COD-aware timing.

    **Wave 2 Phase 13 — Multi-pixel fan-out.** When the store has
    multiple pixels configured (``store.settings.tracking.meta.pixels``),
    enqueues one task per capi-enabled pixel. Each pixel is a separate
    Meta dedup namespace, so the SAME ``event_id`` is used across all
    fan-out copies — Pixel and CAPI collapse within each pixel's
    Events Manager. Backward-compatible: stores with only the legacy
    single ``pixel_id`` set still get exactly one enqueue.

    Dedup contract: ``event_id`` defaults to ``str(order.id)`` for
    ``Purchase`` (matches the storefront's browser-side fire so Meta
    collapses them within ~48h). For non-Purchase events (e.g.
    ``Lead`` on COD-confirmation), defaults to a prefixed form
    ``f"{event_name_lower}-{order.id}"`` so Lead and Purchase dedupe
    separately within Meta's window.
    """
    # Lazy imports — the Celery task module pulls in the full HTTP
    # client + signing stack; the repository imports the SQLAlchemy
    # tenant model. Keeping them lazy means webhook handlers without
    # CAPI configured pay zero import cost on cold start.
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.messaging.tasks.meta_capi import meta_capi_send_event
    from src.infrastructure.repositories.store_repository import StoreRepository

    sr = StoreRepository(db)
    store_id_uuid = (
        order.store_id
        if isinstance(order.store_id, UUID)
        else UUID(str(order.store_id))
    )
    store = await sr.get_by_id(store_id_uuid)
    if store is None:
        return
    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}

    pixels = resolve_pixels(meta_cfg, mode="capi")
    if not pixels:
        return

    if event_id is None:
        event_id = (
            str(order.id)
            if event_name == "Purchase"
            else f"{event_name.lower()}-{order.id}"
        )

    paid_at = getattr(order, "paid_at", None) or datetime.now(UTC)
    user_data = _build_user_data_from_order(order)
    custom_data = _build_custom_data_from_order(order)
    event_time = int(paid_at.timestamp())

    # Fan out: same event_id across pixels (each pixel is its own Meta
    # dedup namespace). Per-pixel tasks are independent Celery jobs so
    # one pixel's 4xx doesn't block the others.
    for pixel in pixels:
        meta_capi_send_event.delay(
            store_id=str(order.store_id),
            pixel_id=pixel.pixel_id,
            event_name=event_name,
            event_id=event_id,
            event_time=event_time,
            event_source_url=None,
            user_data=user_data,
            custom_data=custom_data,
            action_source="website",
        )


async def enqueue_meta_capi_purchase(db: AsyncSession, order: Any) -> None:
    """Enqueue a Purchase CAPI event for ``order``, gated on store config.

    Thin wrapper around ``enqueue_meta_capi_event_for_order`` preserved
    for the existing payment-webhook callers (Paymob, Fawry, Fawaterak,
    Instapay, Kashier, COD).
    """
    await enqueue_meta_capi_event_for_order(db, order, event_name="Purchase")


async def enqueue_meta_capi_refund(db: AsyncSession, order: Any) -> None:
    """Wave 2 Phase 21 — fire a Meta CAPI Refund custom event.

    Sends ``event_name="Refund"`` with a NEGATIVE ``value`` so the
    merchant can build a Meta Ads Manager custom report subtracting
    Refund from Purchase to see real net revenue. Meta's native CAPI
    spec has no built-in refund event (Shopify doesn't fire one either,
    so Meta-reported revenue diverges from actuals platform-wide) —
    this custom event closes the gap.

    Dedup contract: ``event_id = f"refund-{order.id}"`` — namespaced
    away from the original Purchase event_id so they appear as separate
    events in Events Manager (the merchant's custom report joins them).

    Fans out to every capi-enabled pixel (Phase 13 multi-pixel parity).
    """
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.messaging.tasks.meta_capi import meta_capi_send_event
    from src.infrastructure.repositories.store_repository import StoreRepository

    sr = StoreRepository(db)
    store_id_uuid = (
        order.store_id
        if isinstance(order.store_id, UUID)
        else UUID(str(order.store_id))
    )
    store = await sr.get_by_id(store_id_uuid)
    if store is None:
        return
    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
    pixels = resolve_pixels(meta_cfg, mode="capi")
    if not pixels:
        return

    custom_data = _build_custom_data_from_order(order)
    # Override the value to be negative — this is the contract Meta
    # custom-event-based refund reports key on. The absolute value is
    # the same as the original Purchase, so the merchant's "Net Meta
    # revenue" report is exactly ``Purchase_total + Refund_total``.
    custom_data["value"] = -abs(custom_data.get("value") or 0.0)
    custom_data["refund_for_order_id"] = str(order.id)

    user_data = _build_user_data_from_order(order)
    event_time = int(datetime.now(UTC).timestamp())
    event_id = f"refund-{order.id}"

    for pixel in pixels:
        meta_capi_send_event.delay(
            store_id=str(order.store_id),
            pixel_id=pixel.pixel_id,
            event_name="Refund",
            event_id=event_id,
            event_time=event_time,
            event_source_url=None,
            user_data=user_data,
            custom_data=custom_data,
            action_source="system_generated",
        )
