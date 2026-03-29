"""Shipment repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.interfaces.repositories.shipment_repository import IShipmentRepository
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.shipment import ShipmentModel


class ShipmentRepository(IShipmentRepository):
    """Shipment repository using SQLAlchemy.

    All queries include tenant_id filter as defense-in-depth alongside RLS.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(ShipmentModel.tenant_id == tid)
        return query

    def _to_entity(self, model: ShipmentModel) -> Shipment:
        """Convert database model to domain entity."""
        return Shipment(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            order_id=model.order_id,
            carrier=model.carrier,
            carrier_shipment_id=model.carrier_shipment_id,
            tracking_number=model.tracking_number,
            tracking_url=model.tracking_url,
            awb_url=model.awb_url,
            status=ShipmentStatus(model.status)
            if model.status
            else ShipmentStatus.PENDING,
            shipment_type=model.shipment_type or "forward",
            parent_shipment_id=model.parent_shipment_id,
            shipping_method=model.shipping_method,
            shipping_cost=model.shipping_cost or 0,
            cod_amount=model.cod_amount or 0,
            cod_collected=model.cod_collected or False,
            cod_collected_at=model.cod_collected_at,
            delivery_attempts=model.delivery_attempts or 0,
            status_history=model.status_history or [],
            metadata=model.extra_data or {},
            shipped_at=model.shipped_at,
            delivered_at=model.delivered_at,
            cancelled_at=model.cancelled_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Shipment) -> ShipmentModel:
        """Convert domain entity to database model."""
        return ShipmentModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            order_id=entity.order_id,
            carrier=entity.carrier,
            carrier_shipment_id=entity.carrier_shipment_id,
            tracking_number=entity.tracking_number,
            tracking_url=entity.tracking_url,
            awb_url=entity.awb_url,
            status=entity.status.value
            if isinstance(entity.status, ShipmentStatus)
            else entity.status,
            shipment_type=entity.shipment_type,
            parent_shipment_id=entity.parent_shipment_id,
            shipping_method=entity.shipping_method,
            shipping_cost=entity.shipping_cost,
            cod_amount=entity.cod_amount,
            cod_collected=entity.cod_collected,
            cod_collected_at=entity.cod_collected_at,
            delivery_attempts=entity.delivery_attempts,
            status_history=entity.status_history,
            extra_data=entity.metadata,
            shipped_at=entity.shipped_at,
            delivered_at=entity.delivered_at,
            cancelled_at=entity.cancelled_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    # ── CRUD ──────────────────────────────────────────────────────────

    async def get_by_id(self, entity_id: UUID) -> Shipment | None:
        query = select(ShipmentModel).where(ShipmentModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Shipment]:
        query = (
            select(ShipmentModel)
            .order_by(ShipmentModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: Shipment) -> Shipment:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Shipment) -> Shipment:
        query = select(ShipmentModel).where(ShipmentModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Shipment with id {entity.id} not found")

        model.carrier = entity.carrier
        model.carrier_shipment_id = entity.carrier_shipment_id
        model.tracking_number = entity.tracking_number
        model.tracking_url = entity.tracking_url
        model.awb_url = entity.awb_url
        model.status = (
            entity.status.value
            if isinstance(entity.status, ShipmentStatus)
            else entity.status
        )
        model.shipment_type = entity.shipment_type
        model.parent_shipment_id = entity.parent_shipment_id
        model.shipping_method = entity.shipping_method
        model.shipping_cost = entity.shipping_cost
        model.cod_amount = entity.cod_amount
        model.cod_collected = entity.cod_collected
        model.cod_collected_at = entity.cod_collected_at
        model.delivery_attempts = entity.delivery_attempts
        model.status_history = entity.status_history
        model.extra_data = entity.metadata
        model.shipped_at = entity.shipped_at
        model.delivered_at = entity.delivered_at
        model.cancelled_at = entity.cancelled_at
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        query = select(ShipmentModel).where(ShipmentModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(ShipmentModel.id)))
        return result.scalar() or 0

    # ── Query Methods ─────────────────────────────────────────────────

    async def get_by_order(self, order_id: UUID) -> list[Shipment]:
        query = (
            select(ShipmentModel)
            .where(ShipmentModel.order_id == order_id)
            .order_by(ShipmentModel.created_at.desc())
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
        carrier: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        has_cod: bool | None = None,
    ) -> list[Shipment]:
        query = select(ShipmentModel).where(ShipmentModel.store_id == store_id)
        if status:
            query = query.where(ShipmentModel.status == status)
        if carrier:
            query = query.where(ShipmentModel.carrier == carrier)
        if date_from:
            query = query.where(ShipmentModel.created_at >= date_from)
        if date_to:
            query = query.where(ShipmentModel.created_at <= date_to)
        if has_cod is True:
            query = query.where(ShipmentModel.cod_amount > 0)
        elif has_cod is False:
            query = query.where(ShipmentModel.cod_amount == 0)
        query = (
            query.order_by(ShipmentModel.created_at.desc()).offset(skip).limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_tracking_number(self, tracking_number: str) -> Shipment | None:
        query = select(ShipmentModel).where(
            ShipmentModel.tracking_number == tracking_number
        )
        # No tenant filter — cross-store lookup for webhooks
        result = await self.session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_tracking_number_for_update(
        self, tracking_number: str
    ) -> Shipment | None:
        query = (
            select(ShipmentModel)
            .where(ShipmentModel.tracking_number == tracking_number)
            .with_for_update()
        )
        result = await self.session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_carrier_shipment_id(
        self, carrier_shipment_id: str
    ) -> Shipment | None:
        query = select(ShipmentModel).where(
            ShipmentModel.carrier_shipment_id == carrier_shipment_id
        )
        result = await self.session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def count_by_store(
        self,
        store_id: UUID,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        query = select(func.count(ShipmentModel.id)).where(
            ShipmentModel.store_id == store_id
        )
        if status:
            query = query.where(ShipmentModel.status == status)
        if date_from:
            query = query.where(ShipmentModel.created_at >= date_from)
        if date_to:
            query = query.where(ShipmentModel.created_at <= date_to)
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    async def get_stats(self, store_id: UUID) -> dict:
        """Aggregate shipment counts by status + COD totals."""
        query = (
            select(
                ShipmentModel.status,
                func.count(ShipmentModel.id).label("count"),
                func.coalesce(func.sum(ShipmentModel.cod_amount), 0).label("cod_total"),
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
                ).label("cod_collected_total"),
            )
            .where(ShipmentModel.store_id == store_id)
            .group_by(ShipmentModel.status)
        )

        result = await self.session.execute(self._tenant_filter(query))
        rows = result.all()

        stats = {
            "total": 0,
            "by_status": {},
            "cod_total": 0,
            "cod_collected": 0,
            "cod_pending": 0,
        }
        for row in rows:
            stats["by_status"][row.status] = row.count
            stats["total"] += row.count
            stats["cod_total"] += row.cod_total
            stats["cod_collected"] += row.cod_collected_total

        stats["cod_pending"] = stats["cod_total"] - stats["cod_collected"]
        return stats

    async def get_cod_summary(
        self,
        store_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict:
        """COD collection summary for reconciliation."""
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
                        ShipmentModel.cod_collected.is_(False)
                        & (ShipmentModel.status == "delivered"),
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

        result = await self.session.execute(self._tenant_filter(query))
        row = result.one()
        return {
            "total_shipments": row.total_shipments,
            "total_expected": row.total_expected,
            "total_collected": row.total_collected,
            "total_pending": row.total_expected - row.total_collected,
            "collected_count": row.collected_count,
            "delivered_not_collected": row.delivered_not_collected,
        }

    async def get_cod_stats_by_store(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict:
        """Get COD shipment statistics for rejection tracking."""
        from sqlalchemy import and_

        query = select(
            func.count().label("total"),
            func.sum(case((ShipmentModel.status == "delivered", 1), else_=0)).label(
                "delivered"
            ),
            func.sum(case((ShipmentModel.status == "failed", 1), else_=0)).label(
                "failed"
            ),
            func.sum(case((ShipmentModel.status == "returned", 1), else_=0)).label(
                "returned"
            ),
            func.sum(ShipmentModel.cod_amount).label("total_cod_amount"),
            func.sum(
                case(
                    (
                        ShipmentModel.status.in_(["failed", "returned"]),
                        ShipmentModel.cod_amount,
                    ),
                    else_=0,
                )
            ).label("rejected_amount"),
        ).where(
            and_(
                ShipmentModel.store_id == store_id,
                ShipmentModel.cod_amount > 0,
                ShipmentModel.created_at >= date_from,
                ShipmentModel.created_at <= date_to,
            )
        )

        result = await self.session.execute(self._tenant_filter(query))
        row = result.one()

        return {
            "total": row.total or 0,
            "delivered": row.delivered or 0,
            "failed": row.failed or 0,
            "returned": row.returned or 0,
            "total_cod_amount": row.total_cod_amount or 0,
            "rejected_amount": row.rejected_amount or 0,
        }

    async def get_cod_rejection_by_location(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Get COD rejection breakdown by shipping location."""
        from sqlalchemy import and_, text

        from src.infrastructure.database.models.tenant.order import OrderModel

        location_expr = func.coalesce(
            OrderModel.shipping_address["city"].astext,
            OrderModel.shipping_address["state"].astext,
            "Unknown",
        ).label("location")

        query = (
            select(
                location_expr,
                func.count().label("total"),
                func.sum(
                    case(
                        (
                            ShipmentModel.status.in_(["failed", "returned"]),
                            1,
                        ),
                        else_=0,
                    )
                ).label("rejected"),
            )
            .join(OrderModel, ShipmentModel.order_id == OrderModel.id)
            .where(
                and_(
                    ShipmentModel.store_id == store_id,
                    ShipmentModel.cod_amount > 0,
                    ShipmentModel.created_at >= date_from,
                    ShipmentModel.created_at <= date_to,
                )
            )
            .group_by(text("location"))
            .order_by(func.count().desc())
        )

        result = await self.session.execute(self._tenant_filter(query))
        rows = result.all()

        locations = []
        for row in rows:
            total = row.total or 0
            rejected = row.rejected or 0
            rate = round((rejected / total) * 100, 1) if total > 0 else 0.0
            locations.append({
                "location": row.location,
                "total": total,
                "rejected": rejected,
                "rate": rate,
            })
        return locations

    async def get_active_shipments(
        self, store_id: UUID | None = None
    ) -> list[Shipment]:
        """Get non-terminal shipments for background sync."""
        terminal = [
            s.value
            for s in [
                ShipmentStatus.DELIVERED,
                ShipmentStatus.RETURNED,
                ShipmentStatus.CANCELLED,
            ]
        ]
        query = select(ShipmentModel).where(
            ShipmentModel.status.notin_(terminal),
            ShipmentModel.tracking_number.isnot(None),
        )
        if store_id:
            query = query.where(ShipmentModel.store_id == store_id)
        query = query.order_by(ShipmentModel.updated_at.asc()).limit(200)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]
