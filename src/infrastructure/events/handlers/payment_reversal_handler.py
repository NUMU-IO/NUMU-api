"""Handlers for un-mark-paid and partial acceptance.

``OrderPaymentReversedEvent`` mirrors ``OrderPaidEvent`` for the side
effects that can be undone: wallet commission, the ETA invoice, the
order timeline and the merchant feed. ``OrderPartiallyAcceptedEvent``
records the door-step outcome on the timeline and the feed. All
best-effort — never raise into the publisher.
"""

from __future__ import annotations

from src.application.services.notification_feed import emit_notification_standalone
from src.core.entities.invoice import InvoiceStatus
from src.core.entities.order_activity import OrderActivityKind
from src.core.events.order_events import (
    OrderPartiallyAcceptedEvent,
    OrderPaymentReversedEvent,
)
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.order_activity import (
    OrderActivityModel,
)
from src.infrastructure.events.handlers.order_activity_handler import (
    _resolve_tenant_id,
)
from src.infrastructure.events.handlers.wallet_commission_handler import (
    reverse_commission_for_order,
)

logger = get_logger(__name__)


async def handle_payment_reversed_commission(event: OrderPaymentReversedEvent) -> None:
    await reverse_commission_for_order(
        order_id=event.order_id,
        order_number=event.order_number,
        store_id=event.store_id,
        note=f"Commission reversal — payment un-marked on order {event.order_number}",
    )


async def handle_payment_reversed_invoice(event: OrderPaymentReversedEvent) -> None:
    """Cancel (never delete) the invoice minted when the order was marked paid."""
    from src.infrastructure.repositories.invoice_repository import InvoiceRepository

    try:
        async with AsyncSessionLocal() as session, session.begin():
            repo = InvoiceRepository(session)
            invoice = await repo.get_by_order_id(event.order_id)
            if invoice is None or invoice.status == InvoiceStatus.CANCELLED:
                return
            invoice.status = InvoiceStatus.CANCELLED
            meta = dict(getattr(invoice, "metadata", None) or {})
            meta["cancelled_reason"] = "payment_reversed"
            if hasattr(invoice, "metadata"):
                invoice.metadata = meta
            await repo.update(invoice)
            logger.info(
                "invoice_cancelled_on_payment_reversal",
                order_id=str(event.order_id),
                invoice_id=str(invoice.id),
            )
    except Exception:
        logger.exception("invoice_cancel_on_payment_reversal_failed")


async def handle_payment_reversed_activity(event: OrderPaymentReversedEvent) -> None:
    try:
        async with AsyncSessionLocal() as session, session.begin():
            tenant_id = await _resolve_tenant_id(session, event.store_id)
            if tenant_id is None:
                return
            session.add(
                OrderActivityModel(
                    tenant_id=tenant_id,
                    store_id=event.store_id,
                    order_id=event.order_id,
                    user_id=event.actor_user_id,
                    kind=OrderActivityKind.SYSTEM_EVENT,
                    event_type="payment_reversed",
                    body=(
                        "Payment un-marked — order is unpaid again"
                        + (f" — {event.reason}" if event.reason else "")
                    ),
                    activity_metadata={
                        "amount_cents": event.amount_cents,
                        "reason": event.reason,
                    },
                )
            )
    except Exception:
        logger.exception("payment_reversed_activity_failed")


async def handle_payment_reversed_notification(
    event: OrderPaymentReversedEvent,
) -> None:
    await emit_notification_standalone(
        store_id=event.store_id,
        category="payments",
        kind="payment.reversed",
        data={
            "order_number": event.order_number,
            "total_cents": event.amount_cents,
            "reason": event.reason,
        },
        link=f"/orders/{event.order_id}",
        entity_type="order",
        entity_id=event.order_id,
        dedupe_key=f"payment.reversed:{event.order_id}:{event.event_id}",
    )


async def handle_partial_acceptance_activity(
    event: OrderPartiallyAcceptedEvent,
) -> None:
    try:
        async with AsyncSessionLocal() as session, session.begin():
            tenant_id = await _resolve_tenant_id(session, event.store_id)
            if tenant_id is None:
                return
            kept = sum(int(ln.get("returned_quantity") or 0) for ln in event.lines)
            session.add(
                OrderActivityModel(
                    tenant_id=tenant_id,
                    store_id=event.store_id,
                    order_id=event.order_id,
                    user_id=event.actor_user_id,
                    kind=OrderActivityKind.SYSTEM_EVENT,
                    event_type="partial_acceptance",
                    body=(
                        f"Customer returned {kept} piece(s) at delivery — "
                        f"collected {event.collected_total_cents / 100:.2f}"
                        + (f" — {event.reason}" if event.reason else "")
                    ),
                    activity_metadata={
                        "lines": event.lines,
                        "returned_value_cents": event.returned_value_cents,
                        "collected_total_cents": event.collected_total_cents,
                        "refund_due_cents": event.refund_due_cents,
                    },
                )
            )
    except Exception:
        logger.exception("partial_acceptance_activity_failed")


async def handle_partial_acceptance_notification(
    event: OrderPartiallyAcceptedEvent,
) -> None:
    await emit_notification_standalone(
        store_id=event.store_id,
        category="logistics",
        kind="order.partial_acceptance",
        data={
            "order_number": event.order_number,
            "total_cents": event.collected_total_cents,
            "returned_value_cents": event.returned_value_cents,
            "refund_due_cents": event.refund_due_cents,
            "returned_count": sum(
                int(ln.get("returned_quantity") or 0) for ln in event.lines
            ),
        },
        link=f"/orders/{event.order_id}",
        entity_type="order",
        entity_id=event.order_id,
        important=event.refund_due_cents > 0,
        dedupe_key=f"order.partial_acceptance:{event.order_id}",
    )
