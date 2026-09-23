"""Server-side funnel event emission for terminal order outcomes.

The storefront emits the upper funnel (page_view → product_view → add_to_cart →
checkout_started → order_completed). The post-purchase ``order_delivered``
step has no client analogue — by the time a courier marks the order
delivered, the customer's browser session is long gone — so we emit it
here, on every code path that flips ``OrderStatus`` to ``DELIVERED``:

* ``UpdateOrderStatusUseCase`` (manual merchant action)
* Bosta / MyLerz / J&T webhook handlers

Idempotent via ``order.metadata["funnel_delivered_recorded"]`` so a webhook
replay or a manual mark following an automated one doesn't double-count.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.application.services.meta_capi_purchase_dispatcher import (
    enqueue_meta_capi_event_for_order,
)
from src.application.services.tiktok_capi_purchase_dispatcher import (
    enqueue_tiktok_capi_event_for_order,
)
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.core.entities.order import Order
    from src.core.interfaces.repositories.order_repository import IOrderRepository
    from src.infrastructure.repositories.funnel_event_repository import (
        FunnelEventRepository,
    )

logger = get_logger(__name__)

_DELIVERED_FLAG = "funnel_delivered_recorded"


async def emit_order_completed(
    order: Order,
    funnel_repo: FunnelEventRepository,
    *,
    payment_method: str,
) -> None:
    """Record an ``order_completed`` funnel event for ``order``.

    For payment paths with a gateway webhook (paymob/kashier/moyasar/
    fawaterak/fawry) the webhook handler emits inline; COD emits at
    checkout. This helper covers the paths that confirm payment inside
    a use case instead — today that is InstaPay proof approval (manual
    merchant review and OCR auto-approve).

    Only fires when the order is fully paid: a deposit payment leaves
    ``payment_status`` PENDING, and its COD parent order already emitted
    ``order_completed`` at checkout. Fail-open — funnel analytics must
    never block a payment confirmation.
    """
    from src.core.entities.order import PaymentStatus

    if order.tenant_id is None:
        return
    if order.payment_status != PaymentStatus.PAID:
        return
    try:
        await funnel_repo.create(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            step="order_completed",
            customer_id=order.customer_id,
            session_fingerprint=order.session_fingerprint,
            step_data={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "total": order.total,
                "payment_method": payment_method,
            },
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "funnel_order_completed_emit_failed",
            order_id=str(order.id),
            error=str(exc),
        )


async def emit_order_delivered(
    order: Order,
    funnel_repo: FunnelEventRepository,
    order_repo: IOrderRepository,
) -> None:
    """Record an ``order_delivered`` funnel event for ``order``, once.

    Fail-open: any persistence error is logged but never re-raised — funnel
    analytics must not block order-status transitions.
    """
    metadata = order.metadata or {}
    if metadata.get(_DELIVERED_FLAG):
        return
    if order.tenant_id is None:
        # Defensive — every persisted order should carry tenant_id; if it
        # doesn't, skip rather than write a row that will fail tenant
        # filtering downstream.
        return

    try:
        await funnel_repo.create(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            step="order_delivered",
            customer_id=order.customer_id,
            step_data={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "total_cents": order.total,
                "currency": order.currency,
            },
        )
        order.metadata = {**metadata, _DELIVERED_FLAG: True}
        await order_repo.update(order)
        await _send_delivered_conversion(funnel_repo.session, order)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "funnel_order_delivered_emit_failed",
            order_id=str(order.id),
            error=str(exc),
        )


async def _send_delivered_conversion(session: Any, order: Order) -> None:
    """Send the COD sale that actually happened: the delivery.

    Its own ``event_id``, so it never deduplicates against the Purchase sent
    at placement. Meta gets a custom ``OrderDelivered`` event (build a custom
    conversion on it to optimise). TikTok cannot optimise on custom events,
    so it gets a standard Purchase on the store's Offline Event Set, when one
    is configured. Runs once per order, behind the delivered flag above.
    """
    event_id = f"delivered-{order.id}"
    try:
        await enqueue_meta_capi_event_for_order(
            session,
            order,
            event_name="OrderDelivered",
            event_id=event_id,
            event_time_now=True,
        )
    except Exception:  # noqa: BLE001 — tracking must not block delivery
        logger.warning(
            "meta_delivered_enqueue_failed", order_id=str(order.id), exc_info=True
        )
    try:
        await enqueue_tiktok_capi_event_for_order(
            session, order, event_name="Purchase", event_id=event_id, offline=True
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "tiktok_delivered_enqueue_failed", order_id=str(order.id), exc_info=True
        )
