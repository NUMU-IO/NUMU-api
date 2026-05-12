"""Event handlers that persist merchant-visible order activity rows.

Distinct from `activity_log_handler` (which writes to the cross-tenant
`audit_logs` table for forensic / admin use). These handlers populate the
tenant-visible `order_activities` table that backs the merchant hub's
order timeline.
"""

from sqlalchemy import select

from src.config.logging_config import get_logger
from src.core.entities.order_activity import OrderActivityKind
from src.core.events.order_events import (
    OrderCreatedEvent,
    OrderPaidEvent,
    OrderStatusChangedEvent,
)
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.order_activity import (
    OrderActivityModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)


async def _resolve_tenant_id(session, store_id) -> object | None:
    """Look up tenant_id for a given store. Returns None if not found."""
    row = await session.execute(select(StoreModel).where(StoreModel.id == store_id))
    store = row.scalar_one_or_none()
    return store.tenant_id if store else None


async def handle_order_created_activity(event: OrderCreatedEvent) -> None:
    """Persist a `system_event` row when a new order is created."""
    async with AsyncSessionLocal() as session, session.begin():
        tenant_id = await _resolve_tenant_id(session, event.store_id)
        if tenant_id is None:
            logger.warning(
                "order_activity_skipped_no_tenant",
                order_id=str(event.order_id),
                event="order.created",
            )
            return

        session.add(
            OrderActivityModel(
                tenant_id=tenant_id,
                store_id=event.store_id,
                order_id=event.order_id,
                user_id=None,
                kind=OrderActivityKind.SYSTEM_EVENT,
                event_type="order_created",
                body=f"Order {event.order_number} placed",
                activity_metadata={
                    "order_number": event.order_number,
                    "total": event.total,
                    "currency": event.currency,
                },
            )
        )


async def handle_order_paid_activity(event: OrderPaidEvent) -> None:
    """Persist a `system_event` row when an order is paid."""
    async with AsyncSessionLocal() as session, session.begin():
        tenant_id = await _resolve_tenant_id(session, event.store_id)
        if tenant_id is None:
            logger.warning(
                "order_activity_skipped_no_tenant",
                order_id=str(event.order_id),
                event="order.paid",
            )
            return

        method = event.payment_method or "unknown"
        session.add(
            OrderActivityModel(
                tenant_id=tenant_id,
                store_id=event.store_id,
                order_id=event.order_id,
                user_id=None,
                kind=OrderActivityKind.SYSTEM_EVENT,
                event_type="order_paid",
                body=f"Payment received via {method}",
                activity_metadata={
                    "payment_method": event.payment_method,
                    "payment_id": event.payment_id,
                    "total": event.total,
                },
            )
        )


async def handle_order_status_changed_activity(
    event: OrderStatusChangedEvent,
) -> None:
    """Persist a `system_event` row when an order's status changes."""
    async with AsyncSessionLocal() as session, session.begin():
        tenant_id = await _resolve_tenant_id(session, event.store_id)
        if tenant_id is None:
            logger.warning(
                "order_activity_skipped_no_tenant",
                order_id=str(event.order_id),
                event="order.status_changed",
            )
            return

        body = (
            f"Order status changed from {event.previous_status} to {event.new_status}"
        )
        if event.reason:
            body += f" — {event.reason}"

        session.add(
            OrderActivityModel(
                tenant_id=tenant_id,
                store_id=event.store_id,
                order_id=event.order_id,
                user_id=None,
                kind=OrderActivityKind.SYSTEM_EVENT,
                event_type="status_changed",
                body=body,
                activity_metadata={
                    "previous_status": event.previous_status,
                    "new_status": event.new_status,
                    "reason": event.reason,
                    "tracking_number": event.tracking_number,
                    "carrier": event.carrier,
                },
            )
        )
