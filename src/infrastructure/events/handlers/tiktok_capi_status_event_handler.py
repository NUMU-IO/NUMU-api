"""COD-aware CompletePayment firing from order-status transitions (TikTok).

Sibling of ``meta_capi_status_event_handler`` — subscribes to
``OrderStatusChangedEvent`` and fires a TikTok Events API ``CompletePayment``
based on the per-store ``purchase_trigger`` config in
``store.settings.tracking.tiktok``:

  * ``purchase_trigger`` (default ``None``) — when set, ``CompletePayment``
    fires on the matching order-status transition. Backward-compatible: when
    ``None``, this handler is a no-op and the payment-webhook path
    (Paymob/Fawry/Fawaterak/Instapay/Kashier + COD collection) remains the sole
    CompletePayment source.

For a COD-heavy store, ``purchase_trigger="delivered"`` means TikTok only sees
real conversions (not no-show COD placements), so ROAS doesn't decay.

Dedup contract preserved: the payment webhook and this handler can both fire for
the same order with the same ``event_id = str(order.id)`` — TikTok collapses
them. For pure COD flow (no payment webhook), only this handler fires.

TikTok has no ``lead_trigger`` (its ``SubmitForm`` is browser-only) and no
server Refund event, so — unlike the Meta handler — this fires only
CompletePayment.
"""

from __future__ import annotations

from sqlalchemy import select

from src.application.services.tiktok_capi_purchase_dispatcher import (
    enqueue_tiktok_capi_event_for_order,
)
from src.core.events.order_events import OrderStatusChangedEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

# Status names that map onto the per-store trigger config. ``paid`` is
# excluded — that's the payment webhooks' job, not status-change events.
_VALID_TRIGGER_STATUSES: frozenset[str] = frozenset({
    "confirmed",
    "processing",
    "shipped",
    "delivered",
})


async def _load_store_and_order(
    session, order_id, store_id
) -> tuple[StoreModel | None, OrderModel | None]:
    """Resolve the store + order in one session (``(None, None)`` if missing)."""
    store = (
        await session.execute(select(StoreModel).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if store is None:
        return None, None
    order = (
        await session.execute(select(OrderModel).where(OrderModel.id == order_id))
    ).scalar_one_or_none()
    return store, order


async def handle_order_status_changed_for_tiktok_capi(
    event: OrderStatusChangedEvent,
) -> None:
    """Fire TikTok CompletePayment when the order hits the configured status.

    Fail-open: a fire that errors must never break the order-status update
    flow. The hourly orphan sweep is the backstop for missed events.
    """
    log = logger.bind(
        order_id=str(event.order_id),
        store_id=str(event.store_id),
        new_status=event.new_status,
        handler="tiktok_capi_status",
    )
    try:
        async with AsyncSessionLocal() as session:
            # Admin/system context — set RLS bypass explicitly (the event
            # already carries tenant-scoped ids; handlers run outside a
            # customer HTTP request). Mirrors the Meta status handler.
            await session.execute(
                __import__("sqlalchemy").text(
                    "SELECT set_config('app.rls_bypass', 'true', true)"
                )
            )
            store, order = await _load_store_and_order(
                session, event.order_id, event.store_id
            )
            if store is None or order is None:
                log.debug("tiktok_capi_status_skipped_no_store_or_order")
                return

            tiktok_cfg = ((store.settings or {}).get("tracking") or {}).get(
                "tiktok"
            ) or {}
            if not (tiktok_cfg.get("api_enabled") and tiktok_cfg.get("pixel_id")):
                log.debug("tiktok_capi_status_skipped_api_off")
                return

            trigger = tiktok_cfg.get("purchase_trigger")
            if trigger not in _VALID_TRIGGER_STATUSES or event.new_status != trigger:
                log.debug(
                    "tiktok_capi_status_no_trigger_match",
                    purchase_trigger=trigger,
                )
                return

            await enqueue_tiktok_capi_event_for_order(
                session, order, event_name="CompletePayment"
            )
            log.info("tiktok_capi_complete_payment_enqueued_from_status_change")
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("tiktok_capi_status_handler_failed", error=str(exc))
