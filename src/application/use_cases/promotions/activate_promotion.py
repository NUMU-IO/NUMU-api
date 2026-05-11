"""ActivatePromotionUseCase / PausePromotionUseCase / ArchivePromotionUseCase.

Three thin wrappers that defer state-transition validation to the
entity's lifecycle methods.
"""

from uuid import UUID

from src.application.dto.promotion import PromotionOutput
from src.application.use_cases.promotions._mapping import promotion_to_output
from src.core.events.base import EventBus
from src.core.events.promotion_events import PromotionUpdatedEvent
from src.core.exceptions.promotion_exceptions import PromotionNotFound
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionDisplayRepository,
    IPromotionRepository,
    IPromotionTargetRepository,
)


class _BaseLifecycleUseCase:
    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        display_repo: IPromotionDisplayRepository,
        target_repo: IPromotionTargetRepository,
        event_bus: EventBus,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._display_repo = display_repo
        self._target_repo = target_repo
        self._event_bus = event_bus

    async def _load(self, tenant_id: UUID, store_id: UUID, promotion_id: UUID):
        promo = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if promo is None or promo.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))
        return promo

    async def _save_and_emit(
        self,
        promo,
        *,
        tenant_id: UUID,
        store_id: UUID,
        actor_user_id: UUID | None,
    ) -> PromotionOutput:
        promo.version += 1
        promo.updated_by = actor_user_id
        saved = await self._promotion_repo.update(promo)
        displays = await self._display_repo.list_for_promotion(saved.id)
        targets = await self._target_repo.list_for_promotion(saved.id)
        self._event_bus.publish(
            PromotionUpdatedEvent(
                promotion_id=saved.id,
                store_id=store_id,
                tenant_id=tenant_id,
                surface=saved.surface.value,
                new_version=saved.version,
                actor_user_id=actor_user_id,
            )
        )
        return promotion_to_output(saved, displays=displays, targets=targets)


class ActivatePromotionUseCase(_BaseLifecycleUseCase):
    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        actor_user_id: UUID | None,
    ) -> PromotionOutput:
        promo = await self._load(tenant_id, store_id, promotion_id)
        promo.activate()
        return await self._save_and_emit(
            promo,
            tenant_id=tenant_id,
            store_id=store_id,
            actor_user_id=actor_user_id,
        )


class PausePromotionUseCase(_BaseLifecycleUseCase):
    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        actor_user_id: UUID | None,
    ) -> PromotionOutput:
        promo = await self._load(tenant_id, store_id, promotion_id)
        promo.pause()
        return await self._save_and_emit(
            promo,
            tenant_id=tenant_id,
            store_id=store_id,
            actor_user_id=actor_user_id,
        )


class ArchivePromotionUseCase(_BaseLifecycleUseCase):
    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        actor_user_id: UUID | None,
    ) -> PromotionOutput:
        promo = await self._load(tenant_id, store_id, promotion_id)
        promo.archive()
        return await self._save_and_emit(
            promo,
            tenant_id=tenant_id,
            store_id=store_id,
            actor_user_id=actor_user_id,
        )
