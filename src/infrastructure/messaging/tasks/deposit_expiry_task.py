"""Celery beat task: cancel orders whose COD deposit window expired.

Runs every 60 seconds. Finds orders in ``PENDING_DEPOSIT`` whose
``deposit_expires_at`` has passed and transitions them to
``CANCELLED`` — the merchant's deposit policy ``ttl_minutes`` is what
determines that expiry time, snapshotted on the order at checkout.
It also cancels and restocks unpaid card-gateway orders
(``AWAITING_PAYMENT``) older than ``AWAITING_PAYMENT_TTL_MINUTES``.

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

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

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


# Unpaid card-gateway checkouts (AWAITING_PAYMENT) are cancelled and restocked
# after this long. ponytail: a payment that lands after the sweep is recorded
# on the cancelled order and flagged in metadata["payment_after_cancel"] for
# the merchant to refund or reopen. Raise the TTL if gateways start settling
# slower than this.
AWAITING_PAYMENT_TTL_MINUTES = 120


async def _sweep() -> dict:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import and_, or_, select

    from src.core.entities.instapay import ManualPaymentIntentStatus
    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.repositories.instapay_intent_repository import (
        ManualPaymentIntentRepository,
    )
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.payment_proof_repository import (
        PaymentProofRepository,
    )
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
                or_(
                    and_(
                        OrderModel.status == OrderStatus.PENDING_DEPOSIT,
                        OrderModel.deposit_expires_at.isnot(None),
                        OrderModel.deposit_expires_at <= now,
                    ),
                    and_(
                        OrderModel.status == OrderStatus.AWAITING_PAYMENT,
                        OrderModel.created_at
                        <= now - timedelta(minutes=AWAITING_PAYMENT_TTL_MINUTES),
                    ),
                )
            )
            # Bound the batch — if the sweeper ever falls behind for any
            # reason, we'd rather process chunks than hold one long txn.
            .limit(500)
        )
        result = await session.execute(query)
        expired_models = result.scalars().all()
        stats["scanned"] = len(expired_models)

        order_repo = OrderRepository(session)
        intent_repo = ManualPaymentIntentRepository(session)

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
                if order.status not in (
                    OrderStatus.PENDING_DEPOSIT,
                    OrderStatus.AWAITING_PAYMENT,
                ):
                    # Race: another path (merchant manual cancel,
                    # gateway webhook arriving just before we got to
                    # it) already moved the order. Nothing to do.
                    continue
                if not order.can_be_cancelled or order.is_paid:
                    continue
                intent = await intent_repo.get_by_order_id(order.id)
                if (
                    intent is not None
                    and intent.status == ManualPaymentIntentStatus.PROOF_RECEIVED
                ):
                    # The customer uploaded a transfer proof that is waiting
                    # for review; cancelling would drop real money.
                    continue
                if order.status == OrderStatus.PENDING_DEPOSIT and (
                    await PaymentProofRepository(session).has_recorded_payments(
                        order.id
                    )
                ):
                    # Money is on the order (ORD-251727): the deposit was
                    # recorded by hand but never confirmed. Leave it for the
                    # merchant rather than cancel and restock a paid order.
                    logger.warning(
                        "pending_deposit_expiry_skipped_recorded_payment",
                        extra={"order_id": str(order.id)},
                    )
                    continue
                order.cancel(
                    reason="COD deposit payment window expired"
                    if order.status == OrderStatus.PENDING_DEPOSIT
                    else "Card payment not completed"
                )
                # Give the debited stock back (idempotent; stamps
                # order.metadata, persisted by the update below).
                try:
                    from src.application.services.stock_service import restock_order

                    await restock_order(session, order, reason="deposit_window_expired")
                except Exception:
                    logger.exception(
                        "pending_deposit_expiry_restock_failed",
                        order_id=str(order.id),
                    )
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
