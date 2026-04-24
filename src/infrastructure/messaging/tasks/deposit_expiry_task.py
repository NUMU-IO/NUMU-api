"""Celery beat task: cancel orders whose COD deposit window expired.

Runs every 60 seconds. Finds orders in ``PENDING_DEPOSIT`` whose
``deposit_expires_at`` has passed and transitions them to
``CANCELLED`` — the merchant's deposit policy ``ttl_minutes`` is what
determines that expiry time, snapshotted on the order at checkout.

The sweeper is the **only** writer for this transition. Read paths
(merchant dashboard, customer order tracking) may briefly show an
order as ``PENDING_DEPOSIT`` past its expiry, up to the 60-second
sweep interval — acceptable lag in exchange for keeping GET requests
side-effect-free.

Each cancel narrows to the order's tenant for the write, mirroring
the ``instapay_expiry_task`` pattern so RLS policies observe the
per-tenant context even though the scan itself runs under bypass.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.expire_pending_deposit_orders",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def expire_pending_deposit_orders_task(self):
    """Scan + cancel. Returns a stats dict for beat logs."""
    try:
        stats = _run_async(_sweep())
        if stats.get("cancelled") or stats.get("errors"):
            logger.info("pending_deposit_expiry_sweep", **stats)
        return stats
    except Exception as exc:
        logger.exception("pending_deposit_expiry_sweep_failed")
        raise self.retry(exc=exc)


async def _sweep() -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    stats = {"scanned": 0, "cancelled": 0, "errors": 0}
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)

        # Scan under bypass — cross-tenant sweep. The index
        # `ix_orders_deposit_expires_at` (partial: WHERE deposit_expires_at
        # IS NOT NULL) keeps this cheap as the orders table grows.
        query = (
            select(OrderModel)
            .where(
                OrderModel.status == OrderStatus.PENDING_DEPOSIT,
                OrderModel.deposit_expires_at.isnot(None),
                OrderModel.deposit_expires_at <= now,
            )
            # Bound the batch — if the sweeper ever falls behind for any
            # reason, we'd rather process chunks than hold one long txn.
            .limit(500)
        )
        result = await session.execute(query)
        expired_models = result.scalars().all()
        stats["scanned"] = len(expired_models)

        order_repo = OrderRepository(session)

        for model in expired_models:
            try:
                # Narrow to the order's tenant for the write — the
                # cancel path goes through repository.update which
                # expects RLS to observe the correct tenant.
                await narrow_to_tenant(session, model.tenant_id)
                order = await order_repo.get_by_id(model.id)
                if order is None:
                    # Shouldn't happen — the scan just saw it — but
                    # stay defensive.
                    continue
                if order.status != OrderStatus.PENDING_DEPOSIT:
                    # Race: another path (merchant manual cancel,
                    # gateway webhook arriving just before we got to
                    # it) already moved the order. Nothing to do.
                    continue
                if not order.can_be_cancelled:
                    continue
                order.cancel(reason="COD deposit payment window expired")
                await order_repo.update(order)
                stats["cancelled"] += 1
            except Exception:
                stats["errors"] += 1
                logger.exception(
                    "pending_deposit_expiry_cancel_failed",
                    order_id=str(model.id),
                )
            finally:
                try:
                    await enable_rls_bypass(session)
                except Exception:
                    logger.exception("pending_deposit_expiry_bypass_reset_failed")

        await session.commit()

    return stats
