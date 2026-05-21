"""ApplyCouponV2UseCase — backward compatibility + promotion targeting."""

from decimal import Decimal

import pytest

from src.application.dto.promotion import CreatePromotionInput
from src.application.dto.promotion_resolution import VisitorContextInput
from src.application.use_cases.promotions.apply_coupon_v2 import (
    ApplyCouponV2UseCase,
)
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.exceptions import EntityNotFoundError
from src.core.services.promotion_eligibility_checker import (
    PromotionEligibilityChecker,
)
from src.core.value_objects.promotion_content import DiscountCodeContent


@pytest.mark.asyncio
async def test_unlinked_coupon_works_like_legacy(
    ids, coupon_repo, promotion_repo, target_repo, event_repo, make_coupon
):
    coupon = await make_coupon("LEGACY10")
    uc = ApplyCouponV2UseCase(
        coupon_repo=coupon_repo,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        event_repo=event_repo,
        eligibility_checker=PromotionEligibilityChecker(),
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        code="LEGACY10",
        order_amount=Decimal("100.00"),
    )
    assert out.coupon_id == coupon.id
    assert out.promotion_id is None
    # legacy flow does not write a redemption event
    assert len(event_repo.rows) == 0
    # usage was incremented
    assert coupon_repo.rows[coupon.id].usage_count == 1


@pytest.mark.asyncio
async def test_unknown_code_raises(
    ids, coupon_repo, promotion_repo, target_repo, event_repo
):
    uc = ApplyCouponV2UseCase(
        coupon_repo=coupon_repo,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        event_repo=event_repo,
        eligibility_checker=PromotionEligibilityChecker(),
    )
    with pytest.raises(EntityNotFoundError):
        await uc.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            code="DOES-NOT-EXIST",
            order_amount=Decimal("100.00"),
        )


@pytest.mark.asyncio
async def test_linked_promotion_records_redemption(
    ids,
    coupon_repo,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    store_repo,
    event_bus,
    event_repo,
    make_coupon,
):
    coupon = await make_coupon("LINKED10")
    create = CreatePromotionUseCase(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    promo = await create.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=CreatePromotionInput(
            name="Welcome",
            surface=PromotionSurface.DISCOUNT_CODE,
            status=PromotionStatus.ACTIVE,
            coupon_id=coupon.id,
            content=DiscountCodeContent(),
        ),
    )

    uc = ApplyCouponV2UseCase(
        coupon_repo=coupon_repo,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        event_repo=event_repo,
        eligibility_checker=PromotionEligibilityChecker(),
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        code="LINKED10",
        order_amount=Decimal("100.00"),
        visitor=VisitorContextInput(),
    )
    assert out.promotion_id == promo.id
    redeems = [e for e in event_repo.rows if e.event_type == "redeem"]
    assert len(redeems) == 1
    assert redeems[0].promotion_id == promo.id
