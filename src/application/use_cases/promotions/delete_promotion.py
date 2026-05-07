"""DeletePromotionUseCase."""

from uuid import UUID

from src.core.events.base import EventBus
from src.core.events.promotion_events import PromotionDeletedEvent
from src.core.exceptions.promotion_exceptions import PromotionNotFound
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
)


class DeletePromotionUseCase:
    """Hard delete. Merchant flow prefers `archive`; this is for admin tooling."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        event_bus: EventBus,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._event_bus = event_bus

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        actor_user_id: UUID | None,
    ) -> None:
        promo = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if promo is None or promo.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))
        await self._promotion_repo.delete(store_id, promotion_id)
        self._event_bus.publish(
            PromotionDeletedEvent(
                promotion_id=promotion_id,
                store_id=store_id,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
            )
        )
