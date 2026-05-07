"""Concrete SQLAlchemy implementations of the Promotion repository protocols."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.exceptions.promotion_exceptions import (
    PromotionConflict,
    PromotionNotFound,
)
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionDisplayRepository,
    IPromotionRepository,
    IPromotionTargetRepository,
    IPromotionTranslationRepository,
)
from src.core.value_objects.localized_promotion_content import (
    LocalizedPromotionContent,
)
from src.infrastructure.database.models.tenant.promotion import (
    PromotionDisplayModel,
    PromotionModel,
    PromotionTargetModel,
    PromotionTranslationModel,
)
from src.infrastructure.mappers.promotion_mapper import PromotionMapper


class PromotionRepository(IPromotionRepository):
    """SQLAlchemy implementation of `IPromotionRepository`."""

    def __init__(
        self,
        session: AsyncSession,
        mapper: PromotionMapper | None = None,
    ) -> None:
        self.session = session
        self.mapper = mapper or PromotionMapper()

    # ------------------------------------------------------------------ #
    # Reads                                                               #
    # ------------------------------------------------------------------ #

    async def _load_translations(
        self, promotion_id: UUID
    ) -> dict[str, LocalizedPromotionContent]:
        stmt = select(PromotionTranslationModel).where(
            PromotionTranslationModel.promotion_id == promotion_id
        )
        result = await self.session.execute(stmt)
        return self.mapper.translations_to_dict(list(result.scalars().all()))

    async def get_by_id(self, store_id: UUID, promotion_id: UUID) -> Promotion | None:
        stmt = select(PromotionModel).where(
            PromotionModel.id == promotion_id,
            PromotionModel.store_id == store_id,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        translations = await self._load_translations(promotion_id)
        return self.mapper.promotion_to_entity(model, translations=translations)

    async def list_for_store(
        self,
        store_id: UUID,
        *,
        status: PromotionStatus | None = None,
        surface: PromotionSurface | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Promotion], int]:
        base_filter = [PromotionModel.store_id == store_id]
        if status is not None:
            base_filter.append(PromotionModel.status == status.value)
        if surface is not None:
            base_filter.append(PromotionModel.surface == surface.value)

        count_stmt = (
            select(func.count()).select_from(PromotionModel).where(*base_filter)
        )
        total = (await self.session.execute(count_stmt)).scalar_one()

        rows_stmt = (
            select(PromotionModel)
            .where(*base_filter)
            .order_by(
                PromotionModel.priority.desc(),
                PromotionModel.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(rows_stmt)).scalars().all()
        items = [self.mapper.promotion_to_entity(m) for m in rows]
        return items, int(total)

    async def list_active_for_storefront(
        self,
        store_id: UUID,
        now: datetime,
        *,
        include_drafts: bool = False,
    ) -> list[Promotion]:
        # `include_drafts` powers the merchant builder preview iframe — we
        # surface DRAFT / SCHEDULED / PAUSED on top of ACTIVE so the
        # merchant can rehearse copy + targeting without flipping a row
        # live. Schedule windows are intentionally NOT enforced in
        # preview mode either, so a "scheduled to start in 3 days" promo
        # is still visible while editing.
        if include_drafts:
            allowed_statuses = (
                PromotionStatus.ACTIVE.value,
                PromotionStatus.DRAFT.value,
                PromotionStatus.SCHEDULED.value,
                PromotionStatus.PAUSED.value,
            )
            where_clauses = [
                PromotionModel.store_id == store_id,
                PromotionModel.status.in_(allowed_statuses),
            ]
        else:
            where_clauses = [
                PromotionModel.store_id == store_id,
                PromotionModel.status == PromotionStatus.ACTIVE.value,
                or_(
                    PromotionModel.starts_at.is_(None),
                    PromotionModel.starts_at <= now,
                ),
                or_(
                    PromotionModel.ends_at.is_(None),
                    PromotionModel.ends_at > now,
                ),
            ]
        stmt = (
            select(PromotionModel)
            .where(*where_clauses)
            .order_by(
                PromotionModel.priority.desc(),
                PromotionModel.created_at.desc(),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return []
        # Bulk-fetch translations for all returned promos in a single query.
        promo_ids = [m.id for m in rows]
        tx_stmt = select(PromotionTranslationModel).where(
            PromotionTranslationModel.promotion_id.in_(promo_ids)
        )
        tx_rows = (await self.session.execute(tx_stmt)).scalars().all()
        by_promo: dict[UUID, list[PromotionTranslationModel]] = {}
        for tx in tx_rows:
            by_promo.setdefault(tx.promotion_id, []).append(tx)
        out: list[Promotion] = []
        for m in rows:
            translations = self.mapper.translations_to_dict(by_promo.get(m.id, []))
            out.append(self.mapper.promotion_to_entity(m, translations=translations))
        return out

    # ------------------------------------------------------------------ #
    # Writes                                                              #
    # ------------------------------------------------------------------ #

    async def create(self, promotion: Promotion) -> Promotion:
        model = self.mapper.promotion_to_orm(promotion)
        self.session.add(model)
        await self.session.flush()
        return self.mapper.promotion_to_entity(model)

    async def update(self, promotion: Promotion) -> Promotion:
        # We don't go through ORM attribute mutation + flush here. That
        # path interacts badly with asyncpg's per-connection ENUM type-OID
        # lookup after a commit boundary (manifests as `MissingGreenlet`
        # during flush). A single `UPDATE ... WHERE id=:id AND version=:v`
        # statement keeps the optimistic lock atomic and avoids the issue.
        new_discount_rule = (
            promotion.discount_rule.model_dump(mode="json")
            if promotion.discount_rule is not None
            else None
        )
        new_content = promotion.content.model_dump(mode="json")
        stmt = (
            sa_update(PromotionModel)
            .where(
                PromotionModel.id == promotion.id,
                PromotionModel.store_id == promotion.store_id,
                PromotionModel.version == promotion.version - 1,
            )
            .values(
                name=promotion.name,
                surface=promotion.surface.value,
                status=promotion.status.value,
                coupon_id=promotion.coupon_id,
                discount_rule=new_discount_rule,
                content=new_content,
                priority=promotion.priority,
                starts_at=promotion.starts_at,
                ends_at=promotion.ends_at,
                updated_by=promotion.updated_by,
                version=promotion.version,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        if result.rowcount == 0:
            # Either the row doesn't exist or someone else bumped the version.
            current = await self.session.get(PromotionModel, promotion.id)
            if current is None or current.store_id != promotion.store_id:
                raise PromotionNotFound(str(promotion.id))
            raise PromotionConflict(current.version, promotion.version - 1)
        # Re-read fresh state for the response.
        refreshed = (
            await self.session.execute(
                select(PromotionModel).where(PromotionModel.id == promotion.id)
            )
        ).scalar_one()
        return self.mapper.promotion_to_entity(refreshed)

    async def bulk_set_priority(
        self, store_id: UUID, items: list[tuple[UUID, int]]
    ) -> None:
        if not items:
            return
        # Single UPDATE … FROM (VALUES …) keeps the round-trip count to
        # one and the version bump deterministic. Filtering by
        # `store_id` inside the WHERE means a forged payload mixing
        # foreign promotion ids has no effect — we just don't touch them.
        from sqlalchemy import bindparam, update

        # Parameterized UPDATE … WHERE id = … (single row) per item is
        # fine here — `items` is bounded at ~200 by the route schema and
        # asyncpg pipelines them on one connection. Avoiding a CTE keeps
        # the SQL portable for tests that point at sqlite.
        for promo_id, priority in items:
            stmt = (
                update(PromotionModel)
                .where(
                    PromotionModel.id == bindparam("pid"),
                    PromotionModel.store_id == bindparam("sid"),
                )
                .values(
                    priority=bindparam("prio"),
                    version=PromotionModel.version + 1,
                )
            )
            await self.session.execute(
                stmt, {"pid": promo_id, "sid": store_id, "prio": priority}
            )
        await self.session.flush()

    async def delete(self, store_id: UUID, promotion_id: UUID) -> None:
        existing = await self.session.get(PromotionModel, promotion_id)
        if existing is None or existing.store_id != store_id:
            raise PromotionNotFound(str(promotion_id))
        await self.session.delete(existing)
        await self.session.flush()


# --------------------------------------------------------------------------- #
# Display / Target / Translation child repos                                  #
# --------------------------------------------------------------------------- #


class PromotionDisplayRepository(IPromotionDisplayRepository):
    def __init__(
        self,
        session: AsyncSession,
        mapper: PromotionMapper | None = None,
    ) -> None:
        self.session = session
        self.mapper = mapper or PromotionMapper()

    async def list_for_promotion(self, promotion_id: UUID) -> list[PromotionDisplay]:
        stmt = (
            select(PromotionDisplayModel)
            .where(PromotionDisplayModel.promotion_id == promotion_id)
            .order_by(PromotionDisplayModel.created_at.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self.mapper.display_to_entity(m) for m in rows]

    async def replace_for_promotion(
        self, promotion_id: UUID, displays: list[PromotionDisplay]
    ) -> list[PromotionDisplay]:
        await self.session.execute(
            delete(PromotionDisplayModel).where(
                PromotionDisplayModel.promotion_id == promotion_id
            )
        )
        for d in displays:
            self.session.add(self.mapper.display_to_orm(d))
        await self.session.flush()
        return await self.list_for_promotion(promotion_id)


class PromotionTargetRepository(IPromotionTargetRepository):
    def __init__(
        self,
        session: AsyncSession,
        mapper: PromotionMapper | None = None,
    ) -> None:
        self.session = session
        self.mapper = mapper or PromotionMapper()

    async def list_for_promotion(self, promotion_id: UUID) -> list[PromotionTarget]:
        stmt = (
            select(PromotionTargetModel)
            .where(PromotionTargetModel.promotion_id == promotion_id)
            .order_by(PromotionTargetModel.created_at.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self.mapper.target_to_entity(m) for m in rows]

    async def replace_for_promotion(
        self, promotion_id: UUID, targets: list[PromotionTarget]
    ) -> list[PromotionTarget]:
        await self.session.execute(
            delete(PromotionTargetModel).where(
                PromotionTargetModel.promotion_id == promotion_id
            )
        )
        for t in targets:
            self.session.add(self.mapper.target_to_orm(t))
        await self.session.flush()
        return await self.list_for_promotion(promotion_id)


class PromotionTranslationRepository(IPromotionTranslationRepository):
    def __init__(
        self,
        session: AsyncSession,
        mapper: PromotionMapper | None = None,
    ) -> None:
        self.session = session
        self.mapper = mapper or PromotionMapper()

    async def get_for_promotion(
        self, promotion_id: UUID
    ) -> dict[str, LocalizedPromotionContent]:
        stmt = select(PromotionTranslationModel).where(
            PromotionTranslationModel.promotion_id == promotion_id
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return self.mapper.translations_to_dict(list(rows))

    async def replace_for_promotion(
        self,
        promotion_id: UUID,
        tenant_id: UUID,
        translations: dict[str, LocalizedPromotionContent],
    ) -> None:
        await self.session.execute(
            delete(PromotionTranslationModel).where(
                PromotionTranslationModel.promotion_id == promotion_id
            )
        )
        for locale, content in translations.items():
            self.session.add(
                self.mapper.translation_to_orm(tenant_id, promotion_id, locale, content)
            )
        await self.session.flush()
