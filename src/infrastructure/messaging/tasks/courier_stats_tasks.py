"""Nightly courier stats rollup task (backend-023 / spec 013).

Refreshes the ``courier_stats`` table for every active store: aggregates
the trailing 30-day window of shipments per carrier and upserts one row
per ``(store_id, carrier, period_start)``.

Idempotent: re-running the task on the same window updates existing rows
in place via INSERT ... ON CONFLICT DO UPDATE; never duplicates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime

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
    name="tasks.courier_stats.refresh_all",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=600,
)
def refresh_courier_stats_all_stores(self) -> dict:
    """Refresh courier_stats for every active store. Beat-scheduled nightly."""
    return _run_async(_refresh_all_stores_async())


async def _refresh_all_stores_async() -> dict:
    from sqlalchemy import select, text

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.shopify_installation import (
        ShopifyInstallationModel,
    )

    refreshed = 0
    failed = 0
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        result = await session.execute(
            select(
                ShopifyInstallationModel.store_id,
                ShopifyInstallationModel.tenant_id,
            ).where(ShopifyInstallationModel.is_active.is_(True))
        )
        installs = list(result.all())

    for store_id, tenant_id in installs:
        try:
            await _refresh_one_store_async(
                store_id=store_id,
                tenant_id=tenant_id,
                snapshot_date=date.today(),
            )
            refreshed += 1
        except Exception as exc:
            logger.warning(
                "courier_stats_refresh_failed_for_store",
                extra={"store_id": str(store_id), "error": str(exc)},
            )
            failed += 1

    return {"refreshed": refreshed, "failed": failed}


@celery_app.task(
    name="tasks.courier_stats.refresh_store",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=60,
)
def refresh_courier_stats_for_store(
    self,
    store_id: str,
    tenant_id: str,
) -> dict:
    """Refresh courier_stats for a single store. On-demand entry point."""
    from uuid import UUID

    return _run_async(
        _refresh_one_store_async(
            store_id=UUID(store_id),
            tenant_id=UUID(tenant_id),
            snapshot_date=date.today(),
        )
    )


async def _refresh_one_store_async(
    *,
    store_id,
    tenant_id,
    snapshot_date: date,
) -> dict:
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from src.application.services.courier_stats_service import (
        ShipmentSnapshot,
        aggregate_shipments,
        rolling_window,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.courier_stats import (
        CourierStatsModel,
    )
    from src.infrastructure.database.models.tenant.shipment import ShipmentModel

    period_start, period_end = rolling_window(end=snapshot_date, days=30)

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        result = await session.execute(
            select(
                ShipmentModel.carrier,
                ShipmentModel.status,
                ShipmentModel.cod_amount,
                ShipmentModel.cod_collected,
                ShipmentModel.shipped_at,
                ShipmentModel.delivered_at,
            ).where(
                ShipmentModel.store_id == store_id,
                ShipmentModel.created_at
                >= datetime.combine(period_start, datetime.min.time(), tzinfo=UTC),
                ShipmentModel.created_at
                < datetime.combine(period_end, datetime.min.time(), tzinfo=UTC),
            )
        )
        shipments = [
            ShipmentSnapshot(
                carrier=row.carrier,
                status=row.status,
                cod_amount=row.cod_amount or 0,
                cod_collected=bool(row.cod_collected),
                shipped_at=row.shipped_at,
                delivered_at=row.delivered_at,
            )
            for row in result.all()
        ]

        per_carrier = aggregate_shipments(shipments)
        now = datetime.now(UTC)
        upserts = 0

        for carrier, agg in per_carrier.items():
            stmt = (
                pg_insert(CourierStatsModel)
                .values(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    carrier=carrier,
                    period_start=period_start,
                    period_end=period_end,
                    total_shipments=agg.total_shipments,
                    delivered_count=agg.delivered_count,
                    returned_count=agg.returned_count,
                    failed_count=agg.failed_count,
                    in_progress_count=agg.in_progress_count,
                    cod_collected_count=agg.cod_collected_count,
                    cod_total_count=agg.cod_total_count,
                    delivery_success_rate=agg.delivery_success_rate,
                    cod_collection_rate=agg.cod_collection_rate,
                    avg_delivery_hours=agg.avg_delivery_hours,
                    last_refreshed_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["store_id", "carrier", "period_start"],
                    set_={
                        "period_end": period_end,
                        "total_shipments": agg.total_shipments,
                        "delivered_count": agg.delivered_count,
                        "returned_count": agg.returned_count,
                        "failed_count": agg.failed_count,
                        "in_progress_count": agg.in_progress_count,
                        "cod_collected_count": agg.cod_collected_count,
                        "cod_total_count": agg.cod_total_count,
                        "delivery_success_rate": agg.delivery_success_rate,
                        "cod_collection_rate": agg.cod_collection_rate,
                        "avg_delivery_hours": agg.avg_delivery_hours,
                        "last_refreshed_at": now,
                        "updated_at": now,
                    },
                )
            )
            await session.execute(stmt)
            upserts += 1

        await session.commit()

    logger.info(
        "courier_stats_refreshed",
        extra={
            "store_id": str(store_id),
            "carriers": upserts,
            "shipments": len(shipments),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        },
    )
    return {
        "store_id": str(store_id),
        "carriers_upserted": upserts,
        "shipments_in_window": len(shipments),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
