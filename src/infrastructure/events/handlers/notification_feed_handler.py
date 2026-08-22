"""EventBus → merchant notification feed.

Sibling of ``order_activity_handler`` (which writes the per-order
timeline). These write the store-wide feed behind the hub's bell.

Each handler reads an order snapshot in its own session, then hands
off to ``emit_notification_standalone`` which writes + commits + fans
out (SSE publish, web-push for important kinds). Best-effort: a failure
here must never surface to the publisher.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from src.application.services.notification_feed import (
    emit_notification_standalone,
)
from src.core.events.order_events import (
    OrderCreatedEvent,
    OrderPaidEvent,
    OrderStatusChangedEvent,
)
from src.core.events.payment_events import PaymentProofSubmittedEvent
from src.core.events.risk_events import TrustKillSwitchFiredEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.order import OrderModel

logger = get_logger(__name__)


def _name_from_address(address: dict | None) -> str | None:
    if not address:
        return None
    first = address.get("first_name") or ""
    last = address.get("last_name") or ""
    name = f"{first} {last}".strip()
    return name or None


async def _order_snapshot(order_id: UUID) -> dict:
    """Customer name + money for the title; empty dict if the order is gone."""
    try:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(
                    OrderModel.order_number,
                    OrderModel.total,
                    OrderModel.currency,
                    OrderModel.payment_method,
                    OrderModel.shipping_address,
                ).where(OrderModel.id == order_id)
            )
            found = row.first()
    except Exception:
        logger.exception("notification_feed_snapshot_failed", order_id=str(order_id))
        return {}
    if found is None:
        return {}
    order_number, total, currency, payment_method, address = found
    return {
        "order_number": order_number,
        "total_cents": int(total or 0),
        "currency": currency,
        "payment_method": payment_method,
        "customer_name": _name_from_address(address),
    }


async def handle_order_created_notification(event: OrderCreatedEvent) -> None:
    snap = await _order_snapshot(event.order_id)
    await emit_notification_standalone(
        store_id=event.store_id,
        category="orders",
        kind="order.new",
        data={
            "order_number": event.order_number,
            "total_cents": snap.get("total_cents", int(event.total * 100)),
            "currency": snap.get("currency") or event.currency,
            "payment_method": snap.get("payment_method"),
            "customer_name": snap.get("customer_name"),
        },
        link=f"/orders/{event.order_id}",
        entity_type="order",
        entity_id=event.order_id,
        dedupe_key=f"order.new:{event.order_id}",
    )


async def handle_order_paid_notification(event: OrderPaidEvent) -> None:
    snap = await _order_snapshot(event.order_id)
    await emit_notification_standalone(
        store_id=event.store_id,
        category="payments",
        kind="payment.received",
        data={
            "order_number": event.order_number,
            "total_cents": snap.get("total_cents", int(event.total * 100)),
            "currency": snap.get("currency") or "EGP",
            "payment_method": event.payment_method or snap.get("payment_method"),
            "customer_name": snap.get("customer_name"),
        },
        link=f"/orders/{event.order_id}",
        entity_type="order",
        entity_id=event.order_id,
        dedupe_key=f"payment.received:{event.order_id}",
    )


# new_status → (category, kind, important)
STATUS_KINDS: dict[str, tuple[str, str, bool]] = {
    "cancelled": ("orders", "order.cancelled", True),
    "payment_failed": ("payments", "payment.failed", True),
    "shipped": ("logistics", "shipment.shipped", False),
    "delivered": ("logistics", "shipment.delivered", False),
    "returned": ("logistics", "shipment.returned", True),
    "refunded": ("payments", "payment.refunded", False),
}


async def handle_order_status_notification(event: OrderStatusChangedEvent) -> None:
    mapping = STATUS_KINDS.get(event.new_status)
    if mapping is None:
        return
    category, kind, important = mapping
    snap = await _order_snapshot(event.order_id)
    await emit_notification_standalone(
        store_id=event.store_id,
        category=category,
        kind=kind,
        data={
            "order_number": event.order_number,
            "customer_name": event.customer_name or snap.get("customer_name"),
            "total_cents": snap.get("total_cents"),
            "currency": snap.get("currency"),
            "previous_status": event.previous_status,
            "reason": event.reason,
            "carrier": event.carrier,
            "tracking_number": event.tracking_number,
        },
        link=f"/orders/{event.order_id}",
        entity_type="order",
        entity_id=event.order_id,
        important=important,
        dedupe_key=f"{kind}:{event.order_id}",
    )


async def handle_kill_switch_notification(event: TrustKillSwitchFiredEvent) -> None:
    await emit_notification_standalone(
        store_id=event.store_id,
        tenant_id=event.tenant_id,
        category="system",
        kind="trust.kill_switch",
        data={
            "rate_pct": event.rate_pct,
            "rto_count": event.rto_count,
            "auto_approve_count": event.auto_approve_count,
            "reason": event.reason,
        },
        link="/trust-network",
        important=True,
        dedupe_key=f"trust.kill_switch:{event.event_id}",
    )


async def handle_payment_proof_submitted_notification(
    event: PaymentProofSubmittedEvent,
) -> None:
    """Manual-rail proof waiting on the merchant — important: money is parked."""
    snap = await _order_snapshot(event.order_id)
    await emit_notification_standalone(
        store_id=event.store_id,
        tenant_id=event.tenant_id,
        category="payments",
        kind="payment.proof_submitted",
        data={
            "order_number": event.order_number or snap.get("order_number"),
            "customer_name": snap.get("customer_name"),
            "total_cents": event.amount_cents or snap.get("total_cents"),
            "currency": event.currency or snap.get("currency"),
            "payment_method": event.payment_method,
            "reference_code": event.reference_code,
        },
        link=f"/orders/{event.order_id}",
        entity_type="order",
        entity_id=event.order_id,
        important=True,
        dedupe_key=f"payment.proof_submitted:{event.proof_id}",
    )
