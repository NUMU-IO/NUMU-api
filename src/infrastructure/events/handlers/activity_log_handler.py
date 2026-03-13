"""Activity log handler for order status changes.

Records every status transition in the audit_logs table so merchants
have a complete, queryable timeline of order events.
"""

from src.config.logging_config import get_logger
from src.core.events.order_events import OrderStatusChangedEvent

logger = get_logger(__name__)


async def handle_activity_log(event: OrderStatusChangedEvent) -> None:
    """Record order status change in the audit log."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.audit import AuditLogModel

    async with AsyncSessionLocal() as session:
        async with session.begin():
            log_entry = AuditLogModel(
                event_type="order.status_changed",
                severity="info",
                store_id=event.store_id,
                resource_type="order",
                resource_id=str(event.order_id),
                action=f"status:{event.previous_status}->{event.new_status}",
                details={
                    "order_number": event.order_number,
                    "previous_status": event.previous_status,
                    "new_status": event.new_status,
                    "reason": event.reason,
                    "customer_id": str(event.customer_id),
                    "tracking_number": event.tracking_number,
                },
            )
            session.add(log_entry)

    logger.info(
        "order_activity_logged",
        order_id=str(event.order_id),
        transition=f"{event.previous_status}->{event.new_status}",
    )
