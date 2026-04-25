"""Celery beat task: auto-flag stale SHIPPED COD orders as RETURNED.

Runs once daily. Manual-ship merchants (no Bosta integration) often
forget to mark order outcomes — without a signal, the cross-merchant
trust network never learns whether a customer actually paid. This
sweep transitions any COD order that has been ``SHIPPED`` for longer
than the merchant's ``cod_trust.auto_rto_days`` window (default 14,
clamped 7-60) to ``RETURNED``, which fires a network ``rto`` event via
``UpdateOrderStatusUseCase``.

Per-store opt-out: setting ``cod_trust.auto_rto_disabled=true`` skips
the merchant entirely. Idempotency: ``order.metadata`` flag prevents
a manually-marked order from being re-flagged here. Each transition
also stamps ``order.metadata["auto_rto_at"]`` so the merchant can
distinguish auto-marks from manual ones in the dashboard.

Mirrors the pattern in ``deposit_expiry_task`` — RLS bypass for the
scan, narrow-to-tenant for each write.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_DEFAULT_AUTO_RTO_DAYS = 14
_MIN_AUTO_RTO_DAYS = 7
_MAX_AUTO_RTO_DAYS = 60

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


def _resolve_auto_rto_days(store_settings: dict | None) -> int:
    """Read the merchant's auto-RTO window, clamped to a sane range."""
    raw = (store_settings or {}).get("cod_trust") or {}
    if not isinstance(raw, dict):
        return _DEFAULT_AUTO_RTO_DAYS
    try:
        days = int(raw.get("auto_rto_days", _DEFAULT_AUTO_RTO_DAYS))
    except (TypeError, ValueError):
        return _DEFAULT_AUTO_RTO_DAYS
    return max(_MIN_AUTO_RTO_DAYS, min(_MAX_AUTO_RTO_DAYS, days))


def _is_auto_rto_disabled(store_settings: dict | None) -> bool:
    raw = (store_settings or {}).get("cod_trust") or {}
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("auto_rto_disabled"))


@celery_app.task(
    name="tasks.auto_rto_stale_shipped_orders",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def auto_rto_stale_shipped_orders_task(self):
    """Sweep stale SHIPPED COD orders. Returns stats dict for beat logs."""
    try:
        stats = _run_async(_sweep())
        if stats.get("flagged") or stats.get("errors"):
            logger.info("auto_rto_sweep", **stats)
        return stats
    except Exception as exc:
        logger.exception("auto_rto_sweep_failed")
        raise self.retry(exc=exc)


async def _sweep() -> dict:
    from sqlalchemy import select

    from src.application.dto.order import UpdateOrderStatusDTO
    from src.application.use_cases.orders.update_order_status import (
        UpdateOrderStatusUseCase,
    )
    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.repositories.customer_repository import (
        CustomerRepository,
    )
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    stats = {"scanned": 0, "flagged": 0, "skipped_disabled": 0, "errors": 0}
    now = datetime.now(UTC)
    # Cap the lookback at the maximum window so the scan stays bounded.
    earliest_cutoff = now - timedelta(days=_MAX_AUTO_RTO_DAYS)
    latest_cutoff = now - timedelta(days=_MIN_AUTO_RTO_DAYS)

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)

        # Coarse scan: any COD order that's been shipped at least the
        # MIN window. Per-store cutoff (which may be larger) is applied
        # after we read the store's settings.
        query = (
            select(OrderModel)
            .where(
                OrderModel.status == OrderStatus.SHIPPED,
                OrderModel.payment_method == "cod",
                OrderModel.shipped_at.isnot(None),
                OrderModel.shipped_at <= latest_cutoff,
                OrderModel.shipped_at >= earliest_cutoff - timedelta(days=365),
            )
            .order_by(OrderModel.shipped_at.asc())
            .limit(500)
        )
        result = await session.execute(query)
        candidates = result.scalars().all()
        stats["scanned"] = len(candidates)

        # Cache store settings to avoid re-fetching for orders from the
        # same store within one sweep.
        store_cache: dict = {}

        for model in candidates:
            try:
                cached = store_cache.get(model.store_id)
                if cached is None:
                    store_q = await session.execute(
                        select(StoreModel).where(StoreModel.id == model.store_id)
                    )
                    store_model = store_q.scalar_one_or_none()
                    if store_model is None:
                        continue
                    cached = (store_model.settings or {}, store_model.owner_id)
                    store_cache[model.store_id] = cached
                store_settings, store_owner_id = cached

                if _is_auto_rto_disabled(store_settings):
                    stats["skipped_disabled"] += 1
                    continue

                per_store_days = _resolve_auto_rto_days(store_settings)
                cutoff = now - timedelta(days=per_store_days)
                if model.shipped_at > cutoff:
                    # Not yet stale per this store's window.
                    continue

                # Narrow to the order's tenant for the write — UseCase
                # goes through repository.update which expects RLS to
                # observe the correct tenant.
                await narrow_to_tenant(session, model.tenant_id)

                order_repo = OrderRepository(session)
                store_repo = StoreRepository(session)
                customer_repo = CustomerRepository(session)
                network_repo = NetworkReputationRepository(session)

                order = await order_repo.get_by_id(model.id)
                if order is None or order.status != OrderStatus.SHIPPED:
                    continue

                # Stamp auto-flag BEFORE the use case so the merchant UI
                # can distinguish auto from manual marks even if the
                # status update fails midway.
                order.metadata = {
                    **(order.metadata or {}),
                    "auto_rto_at": now.isoformat(),
                    "auto_rto_days": per_store_days,
                }
                await order_repo.update(order)

                use_case = UpdateOrderStatusUseCase(
                    order_repository=order_repo,
                    store_repository=store_repo,
                    customer_repository=customer_repo,
                    event_bus=None,
                    network_repository=network_repo,
                )
                # Run as the store owner so the use case's authorization
                # check passes — this is a system-driven transition the
                # merchant has opted into via cod_trust settings.
                await use_case.execute(
                    order_id=order.id,
                    dto=UpdateOrderStatusDTO(
                        status="returned",
                        reason="auto_rto_stale_shipped",
                    ),
                    store_id=order.store_id,
                    user_id=store_owner_id,
                )
                stats["flagged"] += 1
            except Exception:
                stats["errors"] += 1
                logger.exception(
                    "auto_rto_flag_failed",
                    order_id=str(model.id),
                )
            finally:
                try:
                    await enable_rls_bypass(session)
                except Exception:
                    logger.exception("auto_rto_bypass_reset_failed")

        await session.commit()

    return stats
