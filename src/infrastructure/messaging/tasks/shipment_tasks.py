"""Celery tasks: shipment status sync and COD reconciliation."""

import asyncio
from datetime import UTC, datetime, timedelta

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """Run async code from a synchronous Celery task using a persistent loop."""
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
    return _task_loop.run_until_complete(coro)


async def _sync_shipments() -> dict:
    """Sync non-terminal shipments that haven't been updated recently."""
    from src.core.entities.shipment import ShipmentStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.bosta.shipping_service import (
        get_bosta_service_for_store,
    )
    from src.infrastructure.repositories.shipment_repository import ShipmentRepository
    from src.infrastructure.repositories.store_repository import StoreRepository

    updated = 0
    errors = 0

    async with AsyncSessionLocal() as session:
        shipment_repo = ShipmentRepository(session)
        store_repo = StoreRepository(session)

        # Get non-terminal shipments, oldest updates first
        active = await shipment_repo.get_active_shipments()

        # Group by store for credential efficiency
        by_store: dict = {}
        for s in active:
            by_store.setdefault(s.store_id, []).append(s)

        for store_id, shipments in by_store.items():
            store = await store_repo.get_by_id(store_id)
            if not store:
                continue

            try:
                bosta_service = await get_bosta_service_for_store(store.settings or {})
            except Exception:
                errors += 1
                continue

            for shipment in shipments:
                if not shipment.tracking_number:
                    continue
                try:
                    tracking = await bosta_service.track_shipment(
                        "Bosta", shipment.tracking_number
                    )
                    # Map tracking status to ShipmentStatus
                    status_map = {
                        "pending": ShipmentStatus.CREATED,
                        "in_transit": ShipmentStatus.IN_TRANSIT,
                        "out_for_delivery": ShipmentStatus.OUT_FOR_DELIVERY,
                        "delivered": ShipmentStatus.DELIVERED,
                        "returned": ShipmentStatus.RETURNED,
                        "cancelled": ShipmentStatus.CANCELLED,
                    }
                    new_status = status_map.get(tracking.status)
                    if new_status and new_status.value != (
                        shipment.status.value
                        if hasattr(shipment.status, "value")
                        else shipment.status
                    ):
                        shipment.update_status(
                            new_status, f"Synced from Bosta API: {tracking.status}"
                        )

                        if new_status == ShipmentStatus.DELIVERED:
                            shipment.delivered_at = datetime.now(UTC)
                        elif new_status == ShipmentStatus.PICKED_UP:
                            shipment.shipped_at = datetime.now(UTC)

                        await shipment_repo.update(shipment)
                        updated += 1

                except Exception as e:
                    logger.warning(
                        "shipment_sync_failed",
                        tracking_number=shipment.tracking_number,
                        error=str(e),
                    )
                    errors += 1

        await session.commit()

    return {"synced": updated, "errors": errors, "total_checked": len(active)}


async def _cod_reconciliation() -> dict:
    """Run COD reconciliation for all stores with active shipments."""
    from src.infrastructure.database.connection import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # Get COD summary across all stores by querying delivered COD shipments
        # from yesterday that haven't been collected
        yesterday = datetime.now(UTC) - timedelta(days=1)
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        from sqlalchemy import and_, case, func, select

        from src.infrastructure.database.models.tenant.shipment import ShipmentModel

        result = await session.execute(
            select(
                ShipmentModel.store_id,
                func.count(ShipmentModel.id).label("total"),
                func.coalesce(func.sum(ShipmentModel.cod_amount), 0).label("expected"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                ShipmentModel.cod_collected.is_(True),
                                ShipmentModel.cod_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("collected"),
            )
            .where(
                and_(
                    ShipmentModel.cod_amount > 0,
                    ShipmentModel.status == "delivered",
                    ShipmentModel.delivered_at >= yesterday,
                    ShipmentModel.delivered_at < today,
                )
            )
            .group_by(ShipmentModel.store_id)
        )
        rows = result.all()

        stores_checked = len(rows)
        total_expected = sum(r.expected for r in rows)
        total_collected = sum(r.collected for r in rows)
        mismatches = sum(1 for r in rows if r.expected != r.collected)

        logger.info(
            "cod_reconciliation_done",
            stores=stores_checked,
            expected=total_expected,
            collected=total_collected,
            mismatches=mismatches,
        )

    return {
        "stores_checked": stores_checked,
        "total_expected_cents": total_expected,
        "total_collected_cents": total_collected,
        "mismatches": mismatches,
    }


@celery_app.task(
    name="tasks.sync_shipment_statuses",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def sync_shipment_statuses(self) -> dict:
    """Sync non-terminal shipment statuses from Bosta API.

    Scheduled every 30 minutes as fallback for missed webhooks.
    """
    try:
        result = _run_async(_sync_shipments())
        logger.info("shipment_sync_task_done", **result)
        return result
    except Exception as exc:
        logger.error("shipment_sync_task_failed", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="tasks.daily_cod_reconciliation",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def daily_cod_reconciliation(self) -> dict:
    """Run daily COD reconciliation across all stores.

    Scheduled at 03:30 UTC (after payment reconciliation at 02:00).
    """
    try:
        result = _run_async(_cod_reconciliation())
        logger.info("cod_reconciliation_task_done", **result)
        return result
    except Exception as exc:
        logger.error("cod_reconciliation_task_failed", error=str(exc))
        raise self.retry(exc=exc)
