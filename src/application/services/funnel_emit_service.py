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

from typing import TYPE_CHECKING

from src.config.logging_config import get_logger

if TYPE_CHECKING:
    from src.core.entities.order import Order
    from src.core.interfaces.repositories.order_repository import IOrderRepository
    from src.infrastructure.repositories.funnel_event_repository import (
        FunnelEventRepository,
    )

logger = get_logger(__name__)

_DELIVERED_FLAG = "funnel_delivered_recorded"


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
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "funnel_order_delivered_emit_failed",
            order_id=str(order.id),
            error=str(exc),
        )
