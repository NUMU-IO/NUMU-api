"""GetPromotionUseCase."""

from uuid import UUID

from src.application.dto.promotion import PromotionOutput
from src.application.use_cases.promotions._mapping import promotion_to_output
from src.core.exceptions.promotion_exceptions import PromotionNotFound
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionDisplayRepository,
    IPromotionRepository,
    IPromotionTargetRepository,
)


class GetPromotionUseCase:
    """Read a single promotion with displays and targets."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        display_repo: IPromotionDisplayRepository,
        target_repo: IPromotionTargetRepository,
        coupon_repo: ICouponRepository | None = None,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._display_repo = display_repo
        self._target_repo = target_repo
        self._coupon_repo = coupon_repo

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
    ) -> PromotionOutput:
        promo = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if promo is None or promo.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))
        displays = await self._display_repo.list_for_promotion(promo.id)
        targets = await self._target_repo.list_for_promotion(promo.id)
        coupon = None
        if promo.coupon_id is not None and self._coupon_repo is not None:
            coupon = await self._coupon_repo.get_by_id(promo.coupon_id)
        return promotion_to_output(
            promo,
            displays=displays,
            targets=targets,
            code=getattr(coupon, "code", None),
            usage_count=getattr(coupon, "usage_count", 0),
        )
