"""Wave 2 Phase 12 — COD-aware Purchase + Lead event firing from status transitions.

Subscribes to ``OrderStatusChangedEvent`` and fires Meta CAPI events
based on the per-store ``purchase_trigger`` / ``lead_trigger`` config in
``store.settings.tracking.meta``:

  * ``purchase_trigger`` (default ``None``) — when set, the Meta
    Purchase event fires on the matching order-status transition.
    Backward-compatible: when ``None``, this handler is a no-op and
    the existing payment-webhook path (Paymob/Fawry/Fawaterak/Instapay/
    Kashier) remains the sole Purchase source.

  * ``lead_trigger`` (default ``None``) — when set, a Meta ``Lead``
    event fires on the matching status transition. Useful for the
    Egyptian COD funnel: ``Lead`` on ``confirmed`` gives Meta's
    algorithm a top-of-funnel signal that doesn't decay ROAS.

Dedup contract preserved: the existing paymob webhook and this handler
can both fire for the same online order (paymob on ``paid``, handler
on ``confirmed``/``shipped``/``delivered``) with the same ``event_id``
``str(order.id)`` for Purchase — Meta collapses them within ~48h.

For pure COD flow (no payment webhook), only this handler fires.
Default behavior is unchanged: stores without ``purchase_trigger`` /
``lead_trigger`` configured see no change.

Plan: ``Plans/meta-pixels&CAPI/Meta-pixels&CAPI.md`` Phase 12.
"""

from __future__ import annotations

from sqlalchemy import select

from src.application.services.meta_capi_purchase_dispatcher import (
    enqueue_meta_capi_event_for_order,
    enqueue_meta_capi_refund,
)
from src.config.logging_config import get_logger
from src.core.events.order_events import OrderStatusChangedEvent
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)


# Status-name set that maps cleanly onto the per-store trigger config.
# ``paid`` is intentionally excluded — it's handled by the payment
# webhooks (Paymob / Fawry / etc.), not by status-change events.
_VALID_TRIGGER_STATUSES: frozenset[str] = frozenset({
    "confirmed",
    "processing",
    "shipped",
    "delivered",
})

# Wave 2 Phase 21 — statuses that trigger a Meta CAPI Refund custom event.
# Always-on when CAPI is enabled — refunds are unambiguously valuable for
# advertisers (lets them reconcile gross vs net revenue without manual work).
# ``returned`` (Bosta RTO) counts as a refund of the value for ad-attribution
# purposes even though no money was refunded — the merchant lost the sale.
_REFUND_STATUSES: frozenset[str] = frozenset({"refunded", "returned"})


async def _load_store_and_order(
    session, order_id, store_id
) -> tuple[StoreModel | None, OrderModel | None]:
    """Resolve the store + order in one session.

    Returns ``(None, None)`` if either is missing — callers degrade to
    a silent no-op (CAPI fires must NEVER raise from event handlers).
    """
    store_row = await session.execute(
        select(StoreModel).where(StoreModel.id == store_id)
    )
    store = store_row.scalar_one_or_none()
    if store is None:
        return None, None
    order_row = await session.execute(
        select(OrderModel).where(OrderModel.id == order_id)
    )
    order = order_row.scalar_one_or_none()
    return store, order


def _resolve_meta_event(
    meta_cfg: dict, new_status: str
) -> tuple[str | None, str | None]:
    """Map ``(purchase_trigger, lead_trigger, new_status)`` → optional event names.

    Returns a tuple ``(purchase_event, lead_event)`` where each entry
    is either the Meta event name to fire (``"Purchase"`` / ``"Lead"``)
    or ``None`` (don't fire). Both can be set if the merchant configured
    both triggers to fire on the same status (rare but valid).
    """
    purchase_trigger = meta_cfg.get("purchase_trigger")
    lead_trigger = meta_cfg.get("lead_trigger")

    purchase_event = None
    lead_event = None
    if purchase_trigger in _VALID_TRIGGER_STATUSES and new_status == purchase_trigger:
        purchase_event = "Purchase"
    if lead_trigger in _VALID_TRIGGER_STATUSES and new_status == lead_trigger:
        lead_event = "Lead"
    return purchase_event, lead_event


async def handle_order_status_changed_for_meta_capi(
    event: OrderStatusChangedEvent,
) -> None:
    """Fire Meta Purchase / Lead based on per-store status trigger config.

    Fail-open: a CAPI fire that errors must never break order-status
    update flow. The hourly orphan-purchase sweep (plan §5.5) is the
    backstop for missed events.
    """
    log = logger.bind(
        order_id=str(event.order_id),
        store_id=str(event.store_id),
        new_status=event.new_status,
        handler="meta_capi_status",
    )
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                # Admin context: handlers run outside an HTTP request so
                # the RLS tenant-context middleware isn't in play. Set it
                # explicitly so the StoreRepository the dispatcher uses
                # can read the store row.
                # We use rls_bypass=true because the OrderStatusChangedEvent
                # already carries the tenant-scoped ids; the handler is
                # a system-trusted boundary, not a customer request.
                __import__("sqlalchemy").text(
                    "SELECT set_config('app.rls_bypass', 'true', true)"
                )
            )
            store, order = await _load_store_and_order(
                session, event.order_id, event.store_id
            )
            if store is None or order is None:
                log.debug("meta_capi_status_skipped_no_store_or_order")
                return

            meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
            if not (meta_cfg.get("capi_enabled") and meta_cfg.get("pixel_id")):
                log.debug("meta_capi_status_skipped_capi_off")
                return

            # Wave 2 Phase 21 — Refund fires unconditionally on refund-
            # like transitions when CAPI is enabled. Independent of the
            # Phase 12 trigger config (those control Lead/Purchase only).
            if event.new_status in _REFUND_STATUSES:
                await enqueue_meta_capi_refund(session, order)
                log.info(
                    "meta_capi_refund_enqueued_from_status_change",
                    status=event.new_status,
                )
                # Refund and Lead/Purchase are mutually exclusive on the
                # same status — if we hit a refund status, we don't
                # also try to fire Lead/Purchase from the trigger map.
                return

            purchase_event, lead_event = _resolve_meta_event(meta_cfg, event.new_status)
            if purchase_event is None and lead_event is None:
                log.debug(
                    "meta_capi_status_no_trigger_match",
                    purchase_trigger=meta_cfg.get("purchase_trigger"),
                    lead_trigger=meta_cfg.get("lead_trigger"),
                )
                return

            if lead_event:
                await enqueue_meta_capi_event_for_order(
                    session, order, event_name="Lead"
                )
                log.info("meta_capi_lead_enqueued_from_status_change")
            if purchase_event:
                await enqueue_meta_capi_event_for_order(
                    session, order, event_name="Purchase"
                )
                log.info("meta_capi_purchase_enqueued_from_status_change")
    except Exception as exc:  # noqa: BLE001 — fail-open per plan §5.5
        log.warning("meta_capi_status_handler_failed", error=str(exc))
