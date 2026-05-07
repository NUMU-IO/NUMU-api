"""SQLAlchemy implementation of `IPromotionDismissalRepository`."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.promotion_dismissal import PromotionDismissal
from src.core.interfaces.repositories.promotion_dismissal_repository import (
    IPromotionDismissalRepository,
)
from src.infrastructure.database.models.tenant.promotion import (
    PromotionDismissalModel,
    PromotionModel,
)
from src.infrastructure.mappers.promotion_mapper import PromotionMapper


class PromotionDismissalRepository(IPromotionDismissalRepository):
    def __init__(
        self,
        session: AsyncSession,
        mapper: PromotionMapper | None = None,
    ) -> None:
        self.session = session
        self.mapper = mapper or PromotionMapper()

    async def record(self, dismissal: PromotionDismissal) -> PromotionDismissal:
        # Idempotent insert via ON CONFLICT DO NOTHING — both partial
        # unique indexes (promo×customer, promo×visitor) are honored.
        values = {
            "id": dismissal.id,
            "tenant_id": dismissal.tenant_id,
            "promotion_id": dismissal.promotion_id,
            "customer_id": dismissal.customer_id,
            "visitor_token": dismissal.visitor_token,
            "dismissed_at": dismissal.dismissed_at,
        }
        stmt = pg_insert(PromotionDismissalModel).values(**values)
        # Postgres lets us target a partial unique index by listing the
        # full set of indexed columns + a WHERE clause; here we just
        # swallow either conflict.
        stmt = stmt.on_conflict_do_nothing()
        await self.session.execute(stmt)
        return dismissal

    async def list_dismissed_promotion_ids(
        self,
        store_id: UUID,
        *,
        customer_id: UUID | None = None,
        visitor_token: str | None = None,
    ) -> set[UUID]:
        if customer_id is None and visitor_token is None:
            return set()

        # Join to promotions to scope by store_id (dismissals don't carry
        # store_id natively).
        clauses = []
        if customer_id is not None:
            clauses.append(PromotionDismissalModel.customer_id == customer_id)
        if visitor_token is not None:
            clauses.append(PromotionDismissalModel.visitor_token == visitor_token)

        stmt = (
            select(PromotionDismissalModel.promotion_id)
            .join(
                PromotionModel,
                PromotionModel.id == PromotionDismissalModel.promotion_id,
            )
            .where(
                PromotionModel.store_id == store_id,
                or_(*clauses),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return set(rows)
