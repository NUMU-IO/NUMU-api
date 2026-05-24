"""Cascade-cancel pending scheduled WhatsApp sends when a related order
moves to cancelled or refunded (FR-016 / US3).

Subscribes to ``OrderStatusChangedEvent``. When the new status is one of
the cancellation states, bulk-updates all pending
``whatsapp_scheduled_sends`` rows where ``related_order_id`` matches the
event's order_id, moving them to ``status='cancelled'`` so the dispatcher
never fires them.
"""

from src.config.logging_config import get_logger
from src.core.events.order_events import OrderStatusChangedEvent

logger = get_logger(__name__)

# Statuses that cascade-cancel scheduled follow-ups. Refunded is included
# alongside cancelled because a refunded order's post-delivery review
# request, win-back nudge, etc. should not fire.
_CASCADE_CANCEL_STATUSES = {"cancelled", "refunded"}


async def handle_order_status_for_scheduled_cancel(
    event: OrderStatusChangedEvent,
) -> None:
    """Cancel pending scheduled sends tied to a cancelled / refunded order."""
    if event.new_status not in _CASCADE_CANCEL_STATUSES:
        return

    from src.application.use_cases.whatsapp.cancel_scheduled_send import (
        CancelScheduledSendUseCase,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import set_tenant_context

    try:
        async with AsyncSessionLocal() as session:
            # Resolve tenant via the order's store so RLS lets us see
            # the scheduled_sends rows. The OrderStatusChangedEvent
            # carries store_id but not tenant_id, so we look it up.
            from sqlalchemy import select

            from src.infrastructure.database.models.tenant.store import StoreModel
            from src.infrastructure.tenancy.rls import RLSBypassContext

            async with RLSBypassContext(session):
                tenant_id = (
                    await session.execute(
                        select(StoreModel.tenant_id).where(
                            StoreModel.id == event.store_id
                        )
                    )
                ).scalar_one_or_none()
            if tenant_id is None:
                logger.warning(
                    "scheduled_cascade_cancel_no_tenant",
                    order_id=str(event.order_id),
                    store_id=str(event.store_id),
                )
                return

            await set_tenant_context(session, tenant_id)
            use_case = CancelScheduledSendUseCase(session)
            cancelled = await use_case.cancel_by_order(event.order_id)
            await session.commit()

            if cancelled > 0:
                logger.info(
                    "whatsapp_scheduled_sends_cascade_cancelled",
                    order_id=str(event.order_id),
                    store_id=str(event.store_id),
                    new_status=event.new_status,
                    cancelled_count=cancelled,
                )
    except Exception as exc:
        # Cascade-cancel is best-effort — a failure here must not
        # propagate to the broader OrderStatusChangedEvent fanout
        # (which already includes email, shipment, webhooks, etc.).
        logger.warning(
            "scheduled_cascade_cancel_failed",
            order_id=str(event.order_id),
            error=str(exc),
        )
