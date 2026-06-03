"""Nightly trust-network reconciliation sweep (P1-1).

Backfills missed ``delivery`` / ``rto`` network-reputation events for
terminal shipments whose order never received the idempotency flag —
dropped webhooks, couriers that don't wire network recording (J&T /
Mylerz), or transient write failures. Courier-agnostic: it heals the moat
no matter which integration dropped the signal.

Idempotent: every successful write stamps ``order.metadata``, so a
re-run only ever fills genuine gaps. Fail-open per store — one store's
error never aborts the sweep.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

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
    name="tasks.trust_network.reconcile_missed_events",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=600,
)
def reconcile_missed_network_events(self, lookback_days: int = 7) -> dict:
    """Backfill missed network events across all active stores.

    Beat-scheduled nightly. ``lookback_days`` bounds the scan to recently
    updated terminal shipments (a couple of days' overlap is plenty to
    catch a dropped webhook; the window is small so the sweep is cheap).
    """
    return _run_async(_reconcile_all_stores_async(lookback_days))


async def _reconcile_all_stores_async(lookback_days: int) -> dict:
    from sqlalchemy import select, text

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.shopify_installation import (
        ShopifyInstallationModel,
    )

    since = datetime.now(UTC) - timedelta(days=max(1, lookback_days))

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        result = await session.execute(
            select(
                ShopifyInstallationModel.store_id,
                ShopifyInstallationModel.tenant_id,
            ).where(ShopifyInstallationModel.is_active.is_(True))
        )
        installs = list(result.all())

    total_backfilled = 0
    stores_scanned = 0
    failed = 0
    for store_id, tenant_id in installs:
        try:
            backfilled = await _reconcile_one_store_async(
                store_id=store_id, tenant_id=tenant_id, since=since
            )
            total_backfilled += backfilled
            stores_scanned += 1
        except Exception as exc:
            logger.warning(
                "trust_reconcile_failed_for_store",
                extra={"store_id": str(store_id), "error": str(exc)},
            )
            failed += 1

    logger.info(
        "trust_reconcile_complete",
        extra={
            "stores_scanned": stores_scanned,
            "events_backfilled": total_backfilled,
            "failed": failed,
        },
    )
    return {
        "stores_scanned": stores_scanned,
        "events_backfilled": total_backfilled,
        "failed": failed,
    }


async def _reconcile_one_store_async(*, store_id, tenant_id, since) -> int:
    from sqlalchemy import select

    from src.application.services.network_reconciliation_service import (
        network_event_to_backfill,
    )
    from src.application.services.network_reputation_service import (
        extract_phone_hash_from_string,
        write_network_event,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.shipment import ShipmentModel
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
    )
    from src.infrastructure.tenancy.rls import narrow_to_tenant

    backfilled = 0
    async with AsyncSessionLocal() as session:
        # narrow_to_tenant activates the tenant context the OrderRepository's
        # _tenant_filter relies on, and is the same path the courier webhooks
        # use to load orders + shipments.
        await narrow_to_tenant(session, tenant_id)

        rows = await session.execute(
            select(
                ShipmentModel.order_id,
                ShipmentModel.status,
            ).where(
                ShipmentModel.store_id == store_id,
                ShipmentModel.status.in_(["delivered", "returned", "rto"]),
                ShipmentModel.updated_at >= since,
            )
        )
        candidates = list(rows.all())
        if not candidates:
            return 0

        order_repo = OrderRepository(session)
        network_repo = NetworkReputationRepository(session)

        for order_id, ship_status in candidates:
            try:
                order = await order_repo.get_by_id(UUID(str(order_id)))
            except Exception:
                order = None
            if order is None:
                continue

            meta = order.metadata or {}
            event_type = network_event_to_backfill(
                shipment_status=ship_status,
                payment_method=order.payment_method,
                delivery_already_recorded=bool(meta.get("network_delivery_recorded")),
                rto_already_recorded=bool(meta.get("network_rto_recorded")),
            )
            if event_type is None:
                continue

            phone = order.shipping_address.phone if order.shipping_address else None
            phone_hash = extract_phone_hash_from_string(phone)
            if not phone_hash:
                continue

            await write_network_event(
                phone_hash=phone_hash,
                store_id=order.store_id,
                event_type=event_type,  # type: ignore[arg-type]
                network_repo=network_repo,
                # Same key the courier + manual paths use, so a backfill can
                # never double-count an outcome a live write already recorded.
                dedup_key=f"{order.store_id}:{order.id}:{event_type}",
            )

            order.metadata[f"network_{event_type}_recorded"] = True
            # Mark provenance so the moat-metrics dashboard can distinguish
            # backfilled events from live ones during DD.
            order.metadata["network_event_backfilled"] = True
            await order_repo.update(order)
            backfilled += 1

        await session.commit()

    if backfilled:
        logger.info(
            "trust_reconcile_store_backfilled",
            extra={"store_id": str(store_id), "events": backfilled},
        )
    return backfilled
