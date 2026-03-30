"""COD (Cash on Delivery) reconciliation service.

Compares expected COD amounts for delivered shipments against
amounts actually collected by the carrier (as reported via webhook).

Detects:
- Delivered shipments where COD was not collected
- Amount mismatches between expected and collected
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.infrastructure.database.models.tenant.shipment import ShipmentModel

logger = get_logger(__name__)


class CodReconciliationService:
    """Reconcile COD shipment payments."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_summary(
        self,
        store_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict:
        """Get COD collection summary for a store.

        Returns:
            dict with total_expected, total_collected, total_pending,
            collected_count, delivered_not_collected
        """
        query = select(
            func.count(ShipmentModel.id).label("total_shipments"),
            func.coalesce(func.sum(ShipmentModel.cod_amount), 0).label(
                "total_expected"
            ),
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
            ).label("total_collected"),
            func.count(
                case(
                    (ShipmentModel.cod_collected.is_(True), ShipmentModel.id),
                )
            ).label("collected_count"),
            func.count(
                case(
                    (
                        and_(
                            ShipmentModel.cod_collected.is_(False),
                            ShipmentModel.status == "delivered",
                        ),
                        ShipmentModel.id,
                    ),
                )
            ).label("delivered_not_collected"),
        ).where(
            ShipmentModel.store_id == store_id,
            ShipmentModel.cod_amount > 0,
        )

        if date_from:
            query = query.where(ShipmentModel.created_at >= date_from)
        if date_to:
            query = query.where(ShipmentModel.created_at <= date_to)

        result = await self.session.execute(query)
        row = result.one()

        return {
            "total_shipments": row.total_shipments,
            "total_expected": row.total_expected,
            "total_collected": row.total_collected,
            "total_pending": row.total_expected - row.total_collected,
            "collected_count": row.collected_count,
            "delivered_not_collected": row.delivered_not_collected,
        }
