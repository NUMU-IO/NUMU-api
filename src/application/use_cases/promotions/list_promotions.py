"""ListPromotionsUseCase — paginated listing with filters."""

from uuid import UUID

from src.application.dto.promotion import (
    PromotionListOutput,
)
from src.application.use_cases.promotions._mapping import promotion_to_list_item
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
)


class ListPromotionsUseCase:
    """Merchant-side paginated listing."""

    def __init__(
        self,
        promotion_repo: IPromotionRepository,
        coupon_repo: ICouponRepository | None = None,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._coupon_repo = coupon_repo

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
        # Enrich discount-code rows with their coupon's code + usage so the
        # merchant "Discounts" table reads like the old coupons list. One
        # store-scoped fetch builds an id→coupon map (stores have few
        # coupons); falls back to bare rows if no coupon repo is wired.
        coupons_by_id: dict = {}
        if self._coupon_repo is not None and any(p.coupon_id for p in items):
            store_coupons = await self._coupon_repo.get_by_store(store_id, limit=500)
            coupons_by_id = {c.id: c for c in store_coupons}
        return PromotionListOutput(
            items=[
                promotion_to_list_item(
                    p,
                    code=getattr(coupons_by_id.get(p.coupon_id), "code", None),
                    usage_count=getattr(
                        coupons_by_id.get(p.coupon_id), "usage_count", 0
                    ),
                )
                for p in items
            ],
            total=total,
            limit=limit,
            offset=offset,
        )
