"""ListPromotionsUseCase — paginated listing with filters."""

from uuid import UUID

from src.application.dto.promotion import (
    PromotionListOutput,
)
from src.application.use_cases.promotions._mapping import promotion_to_list_item
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
)


class ListPromotionsUseCase:
    """Merchant-side paginated listing."""

    def __init__(self, promotion_repo: IPromotionRepository) -> None:
        self._promotion_repo = promotion_repo

    async def execute(
        self,
        *,
        store_id: UUID,
        status: PromotionStatus | None = None,
        surface: PromotionSurface | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PromotionListOutput:
        items, total = await self._promotion_repo.list_for_store(
            store_id,
            status=status,
            surface=surface,
            limit=limit,
            offset=offset,
        )
        return PromotionListOutput(
            items=[promotion_to_list_item(p) for p in items],
            total=total,
            limit=limit,
            offset=offset,
        )
