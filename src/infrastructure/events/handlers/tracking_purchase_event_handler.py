"""One server Purchase per order, at the moment the order becomes a sale.

- COD orders: when the order is created. The shopper's IP, user agent and
  click ids were snapshotted on the order by the checkout request.
- Manual rails (InstaPay, Vodafone Cash): when the payment proof is approved,
  so unpaid and abandoned orders are never reported as sales. These orders
  never render the thank-you page, so this is their only Purchase.
- Card orders skip ``OrderCreatedEvent`` and are sent by their payment
  webhooks.
- Merchant-created and TikTok Shop orders are skipped (see below).

``event_id`` is ``str(order.id)`` on every leg, so the browser copy, the
webhook copy and the sweep copy all deduplicate against this one.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from src.application.services.meta_capi_purchase_dispatcher import (
    enqueue_meta_capi_event_for_order,
)
from src.application.services.tiktok_capi_purchase_dispatcher import (
    enqueue_tiktok_capi_event_for_order,
)
from src.core.events.order_events import OrderCreatedEvent
from src.core.events.payment_events import PaymentProofApprovedEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.order import OrderModel

logger = get_logger(__name__)


async def _send_purchase(order_id: UUID, *, only_cod: bool) -> None:
    log = logger.bind(order_id=str(order_id), handler="tracking_purchase")
    try:
        async with AsyncSessionLocal() as session:
            # Handlers run outside a request; the event carries trusted ids.
            await session.execute(
                text("SELECT set_config('app.rls_bypass', 'true', true)")
            )
            order: Any = (
                await session.execute(
                    select(OrderModel).where(OrderModel.id == order_id)
                )
            ).scalar_one_or_none()
            # Only storefront checkouts: they alone snapshot the shopper's
            # browser. Merchant-created and TikTok Shop orders publish the
            # same event but have no web identity, and TikTok Shop sales are
            # already attributed by TikTok.
            if order is None or not (order.extra_data or {}).get("user_agent"):
                return
            if only_cod and (order.payment_method or "cod") != "cod":
                return
            for enqueue in (
                enqueue_meta_capi_event_for_order,
                enqueue_tiktok_capi_event_for_order,
            ):
                try:
                    await enqueue(session, order, event_name="Purchase")
                except Exception:
                    log.warning("tracking_purchase_enqueue_failed", exc_info=True)
    except Exception:
        # Tracking must never break order creation or proof approval; the
        # hourly sweeps and the daily gap alert are the backstop.
        log.warning("tracking_purchase_failed", exc_info=True)


async def handle_order_created_purchase(event: OrderCreatedEvent) -> None:
    await _send_purchase(event.order_id, only_cod=True)


async def handle_payment_proof_approved_purchase(
    event: PaymentProofApprovedEvent,
) -> None:
    await _send_purchase(event.order_id, only_cod=False)
