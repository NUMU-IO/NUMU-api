"""Merchant push notification handler for new orders.

A SEPARATE handler from ``merchant_notification_handler`` (email), on purpose:
that one returns early on ``store.settings.email_notifications.new_order``,
and a merchant who turned off order EMAILS has not asked to stop their phone
buzzing. Different channel, different preference.

This is the feature that replaces the merchant hub's 60-second
``NewOrderNotifier`` poll — which only ever worked while a browser tab was
open. In a COD market the speed of the confirmation call drives delivery
success, so "the merchant finds out within seconds, without watching a tab"
is a commercial outcome, not a technical one.

Best-effort throughout: a push failure must never affect order creation.
"""

from sqlalchemy import select

from src.core.events.order_events import OrderCreatedEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)


def _format_amount(total_cents: int | None, currency: str | None, is_ar: bool) -> str:
    """Money for a lock screen: whole units, no decimals, localised suffix."""
    value = (total_cents or 0) / 100
    formatted = f"{value:,.0f}"
    cur = (currency or "EGP").upper()
    if is_ar and cur == "EGP":
        return f"{formatted} ج.م"
    return f"{cur} {formatted}"


async def handle_merchant_order_push(event: OrderCreatedEvent) -> None:
    """Notify the store's devices that a new order arrived."""
    async with AsyncSessionLocal() as session:
        store: StoreModel | None = (
            await session.execute(
                select(StoreModel).where(StoreModel.id == event.store_id)
            )
        ).scalar_one_or_none()
        if store is None:
            logger.warning(
                "merchant_order_push_skipped",
                order_id=str(event.order_id),
                reason="store_not_found",
            )
            return

        store_settings = store.settings or {}
        push_prefs = store_settings.get("push_notifications", {}) or {}
        # Absent key means enabled — opt-out, not opt-in, matching the email
        # channel's convention.
        if not push_prefs.get("new_order", True):
            logger.info(
                "merchant_order_push_skipped",
                order_id=str(event.order_id),
                store_id=str(event.store_id),
                reason="merchant_opted_out",
            )
            return

        order: OrderModel | None = (
            await session.execute(
                select(OrderModel).where(OrderModel.id == event.order_id)
            )
        ).scalar_one_or_none()
        if order is None:
            logger.warning(
                "merchant_order_push_skipped",
                order_id=str(event.order_id),
                reason="order_not_found",
            )
            return

        tenant_id = getattr(store, "tenant_id", None)
        if tenant_id is None:
            logger.warning(
                "merchant_order_push_skipped",
                order_id=str(event.order_id),
                reason="no_tenant",
            )
            return

        is_ar = not (store.default_language or "ar").lower().startswith("en")
        amount = _format_amount(order.total, order.currency or event.currency, is_ar)
        number = order.order_number or str(order.id)[:8]

        # ─── PAYLOAD: order number + amount ONLY ────────────────────────────
        # This renders on a lock screen, which merchants hand to staff and
        # couriers. No customer name, phone, email or address — ever. The
        # merchant taps through and reads the details behind their own session.
        title = f"طلب جديد #{number}" if is_ar else f"New order #{number}"
        body = amount
        url = f"/orders/{order.id}"
        # Collapses duplicates at the OS level, which is what makes a Celery
        # retry safe: it replaces the notification instead of stacking a second.
        tag = f"order-{order.id}"

        try:
            from src.infrastructure.messaging.tasks.push_tasks import (
                send_push_notification_task,
            )

            send_push_notification_task.delay(
                tenant_id=str(tenant_id),
                title=title,
                body=body,
                url=url,
                tag=tag,
                # v1 notifies the owner. Broader staff scoping is a follow-up:
                # it needs the staff/roles model to say who may see orders.
                user_ids=[str(store.owner_id)] if store.owner_id else None,
            )
            logger.info(
                "merchant_order_push_queued",
                order_id=str(event.order_id),
                store_id=str(event.store_id),
            )
        except Exception as exc:  # noqa: BLE001 - never break order creation
            logger.warning(
                "merchant_order_push_enqueue_failed",
                order_id=str(event.order_id),
                error=str(exc),
            )
