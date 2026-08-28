"""Single source of truth for TikTok Events API CompletePayment fan-out
across all payment-confirmation paths (Paymob, Fawry, Fawaterak, Instapay,
Kashier, COD). Sibling of ``meta_capi_purchase_dispatcher``.

CompletePayment is server-authoritative: the browser-side
``ttq.track('CompletePayment', …, { event_id: order.id })`` fire is
best-effort; the webhook hook here is the one TikTok optimizes ad spend
against.

Dedup contract:
    event_id = str(order.id)

The storefront's order-confirmation page passes the same id when it fires
CompletePayment from the browser, so Pixel + Events API collapse to one
event in TikTok's Events Manager.

All callers should:
    try:
        await enqueue_tiktok_capi_purchase(db, order)
    except Exception:
        log.warning("tiktok_capi_purchase_enqueue_failed", exc_info=True)

Failures must NEVER fail the webhook. The hourly orphan sweep catches
missed events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


def _build_user_data_from_order(order: Any) -> dict[str, Any]:
    """Extract TikTok Events API user_data from an Order.

    PII is SHA-256-hashed downstream (``tiktok/hashing.py``); we forward
    raw values here. ``ttclid`` / ``ttp`` are pulled from the order
    metadata snapshot captured at checkout-create time — the checkout
    route (``storefront/checkout.py``) reads the ``ttclid`` / ``_ttp``
    cookies off the buyer's request and stamps them onto the order, so
    the server-side conversion carries the click id even when the browser
    fire is blocked. They are absent on orders created before that
    snapshot shipped, and on paths with no browser request at all
    (merchant-created / imported orders) — TikTok drops null fields.

    ``email`` is deliberately None here and resolved from the customer record
    by ``fill_identity_from_customer`` at the call site — the same correction
    the Meta sibling already carries. ``OrderShippingAddress`` has no email
    field and never has, so ``shipping.get("email")`` was a permanent None and
    every server-side CompletePayment reached TikTok with no email match key
    at all.
    """
    from src.infrastructure.external_services.meta.country_iso import (
        canonicalize_country,
    )

    shipping = order.shipping_address or {}
    meta = getattr(order, "metadata", None) or {}
    raw_country = shipping.get("country_code") or shipping.get("country")
    return {
        # Resolved from the customer row by `fill_identity_from_customer`;
        # see the docstring.
        "email": None,
        "phone": shipping.get("phone"),
        "first_name": shipping.get("first_name"),
        "last_name": shipping.get("last_name"),
        "city": shipping.get("city"),
        "country_code": canonicalize_country(raw_country),
        "zip": shipping.get("postal_code") or shipping.get("zip"),
        "customer_id": str(order.customer_id) if order.customer_id else None,
        # The session fingerprint the mid-funnel events already sent as
        # `external_id`. `_first_external_id` falls back to this key when
        # there is no customer id, so without it a GUEST order — the majority
        # of MENA COD checkouts — sent TikTok no external_id at all and the
        # conversion could not be joined to the browsing session TikTok had
        # already seen. Mirrors the Meta sibling.
        "external_id": getattr(order, "session_fingerprint", None),
        "ip": meta.get("ip_address"),
        "user_agent": meta.get("user_agent"),
        "ttclid": meta.get("ttclid"),
        "ttp": meta.get("ttp"),
    }


def _build_custom_data_from_order(
    order: Any, catalog_ids: dict[str, str] | None = None
) -> dict[str, Any]:
    """Build the Meta-shaped ``custom_data`` for an Order.

    The Celery task transforms this into TikTok's ``properties`` shape (see
    ``_to_tiktok_properties``), so call sites stay provider-agnostic.

    ``catalog_ids`` maps product_id → the merchant's catalog id; see
    ``meta_capi_purchase_dispatcher.resolve_catalog_ids``. TikTok's
    catalogue is fed from the same product feed, so a content id that does
    not exist in the feed breaks TikTok's video shopping ads the same way
    it breaks Meta's dynamic ads.
    """
    line_items = order.line_items or []
    catalog = catalog_ids or {}

    def _content_id(li: dict) -> str:
        pid = str(li.get("product_id", ""))
        return catalog.get(pid, pid)

    contents = [
        {
            "id": _content_id(li),
            "quantity": int(li.get("quantity", 1)),
            "item_price": int(li.get("unit_price", 0)) / 100,
        }
        for li in line_items
        if li.get("product_id")
    ]
    data: dict[str, Any] = {
        "value": (order.total or 0) / 100,
        "currency": order.currency or "EGP",
        "content_ids": [_content_id(li) for li in line_items if li.get("product_id")],
        "content_type": "product",
        "contents": contents,
        "num_items": sum(int(li.get("quantity", 1)) for li in line_items),
        "order_id": str(order.id),
    }
    return data


async def enqueue_tiktok_capi_event_for_order(
    db: AsyncSession,
    order: Any,
    *,
    event_name: str = "CompletePayment",
    event_id: str | None = None,
) -> None:
    """Enqueue any TikTok Events API event for an order, gated on config.

    Multi-pixel fan-out: one task per api-enabled pixel. Each pixel is a
    separate TikTok dedup namespace, so the SAME ``event_id`` is used
    across all fan-out copies.

    Dedup contract: ``event_id`` defaults to ``str(order.id)`` for
    CompletePayment (matches the browser fire). For other events it
    defaults to ``f"{event_name_lower}-{order.id}"``.
    """
    from src.application.services.tiktok_pixel_resolver import resolve_tiktok_pixels
    from src.infrastructure.messaging.tasks.tiktok_capi import tiktok_capi_send_event
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
    tiktok_cfg = ((store.settings or {}).get("tracking") or {}).get("tiktok") or {}

    pixels = resolve_tiktok_pixels(tiktok_cfg, mode="api")
    if not pixels:
        return

    if event_id is None:
        event_id = (
            str(order.id)
            if event_name == "CompletePayment"
            else f"{event_name.lower()}-{order.id}"
        )

    from src.application.services.meta_capi_purchase_dispatcher import (
        fill_identity_from_customer,
        resolve_catalog_ids,
    )

    paid_at = getattr(order, "paid_at", None) or datetime.now(UTC)
    user_data = _build_user_data_from_order(order)
    # Same customer-record enrichment the Meta Purchase path runs. Reused
    # verbatim: it writes `email` / `phone` / `first_name` / `last_name`, which
    # are exactly the raw keys TikTok's `hash_user_data` reads.
    await fill_identity_from_customer(db, user_data, order)
    custom_data = _build_custom_data_from_order(
        order, await resolve_catalog_ids(db, order)
    )
    event_time = int(paid_at.timestamp())

    # TikTok reads this as ``page.url`` and attributes the conversion with
    # it. The Celery task falls back to the store origin, but we have the
    # store and the order here, so send the real confirmation page
    # (numu-storefront: app/[domain]/checkout/[order_id]/thank-you). Guarded
    # because an unresolvable store origin would yield a malformed URL,
    # which is worse than sending none.
    store_origin = getattr(store, "store_url", None)
    event_source_url = (
        f"{store_origin}/checkout/{order.id}/thank-you" if store_origin else None
    )

    for pixel in pixels:
        tiktok_capi_send_event.delay(
            store_id=str(order.store_id),
            pixel_id=pixel.pixel_id,
            event_name=event_name,
            event_id=event_id,
            event_time=event_time,
            event_source_url=event_source_url,
            user_data=user_data,
            custom_data=custom_data,
            action_source="web",
        )


async def enqueue_tiktok_capi_purchase(db: AsyncSession, order: Any) -> None:
    """Enqueue a CompletePayment event for ``order``, gated on store config.

    Thin wrapper preserved for the payment-webhook callers (Paymob, Fawry,
    Fawaterak, Instapay, Kashier, COD).
    """
    await enqueue_tiktok_capi_event_for_order(db, order, event_name="CompletePayment")
