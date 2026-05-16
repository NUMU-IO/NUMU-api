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


async def enqueue_meta_capi_purchase(db: AsyncSession, order: Any) -> None:
    """Enqueue a Purchase CAPI event for ``order``, gated on store config.

    Looks up the store, checks ``capi_enabled`` + ``pixel_id`` are set,
    then ships a Celery task with the canonical payload shape every
    payment-method handler shares.
    """
    # Lazy imports — the Celery task module pulls in the full HTTP
    # client + signing stack; the repository imports the SQLAlchemy
    # tenant model. Keeping them lazy means webhook handlers without
    # CAPI configured pay zero import cost on cold start.
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
    pixel_id = meta_cfg.get("pixel_id")
    if not (meta_cfg.get("capi_enabled") and pixel_id):
        return

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

    # Match-quality user_data. The Meta CAPI client SHA-256-hashes
    # PII downstream (see ``meta/hashing.py``) — we forward raw values
    # here so the dispatcher stays oblivious to that contract.
    shipping = order.shipping_address or {}
    user_data = {
        "email": shipping.get("email"),
        "phone": shipping.get("phone"),
        "first_name": shipping.get("first_name"),
        "last_name": shipping.get("last_name"),
        "city": shipping.get("city"),
        "country_code": shipping.get("country") or shipping.get("country_code"),
        "zip": shipping.get("postal_code") or shipping.get("zip"),
        "customer_id": str(order.customer_id) if order.customer_id else None,
    }

    paid_at = getattr(order, "paid_at", None) or datetime.now(UTC)

    meta_capi_send_event.delay(
        store_id=str(order.store_id),
        pixel_id=pixel_id,
        event_name="Purchase",
        event_id=str(order.id),
        event_time=int(paid_at.timestamp()),
        event_source_url=None,
        user_data=user_data,
        custom_data={
            "value": (order.total or 0) / 100,
            "currency": order.currency or "EGP",
            "content_ids": [
                str(li.get("product_id")) for li in line_items if li.get("product_id")
            ],
            "content_type": "product",
            "contents": contents,
            "num_items": sum(int(li.get("quantity", 1)) for li in line_items),
            "order_id": str(order.id),
        },
        action_source="website",
    )
