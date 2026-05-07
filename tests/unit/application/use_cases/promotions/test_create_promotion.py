"""CreatePromotionUseCase — happy path + failure cases."""

import pytest

from src.application.dto.promotion import (
    CreatePromotionInput,
    PromotionDisplayInput,
    PromotionTargetInput,
)
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.core.enums.promotion_enums import (
    DisplayFrequency,
    DisplayTrigger,
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.exceptions import EntityNotFoundError
from src.core.exceptions.promotion_exceptions import CouponPromotionLinkError
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    AutomaticContent,
    DiscountCodeContent,
)


def _build(**deps):
    return CreatePromotionUseCase(
        promotion_repo=deps["promotion_repo"],
        display_repo=deps["display_repo"],
        target_repo=deps["target_repo"],
        translation_repo=deps["translation_repo"],
        coupon_repo=deps["coupon_repo"],
        store_repo=deps["store_repo"],
        event_bus=deps["event_bus"],
    )


@pytest.mark.asyncio
async def test_create_announcement_bar_happy_path(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Free shipping bar",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        status=PromotionStatus.ACTIVE,
        content=AnnouncementBarContent(),
        displays=[
            PromotionDisplayInput(
                trigger=DisplayTrigger.ALWAYS,
                frequency=DisplayFrequency.UNTIL_DISMISSED,
            )
        ],
        targets=[
            PromotionTargetInput(
                target_kind=TargetKind.AUDIENCE,
                target_value={"kind": "all"},
            )
        ],
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )
    assert out.surface == PromotionSurface.ANNOUNCEMENT_BAR
    assert out.status == PromotionStatus.ACTIVE
    assert len(out.displays) == 1
    assert len(out.targets) == 1


@pytest.mark.asyncio
async def test_create_discount_code_requires_existing_coupon(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    make_coupon,
):
    coupon = await make_coupon()
    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Welcome",
        surface=PromotionSurface.DISCOUNT_CODE,
        coupon_id=coupon.id,
        content=DiscountCodeContent(),
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )
    assert out.coupon_id == coupon.id


@pytest.mark.asyncio
async def test_create_discount_code_with_unknown_coupon_fails(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    from uuid import uuid4

    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Welcome",
        surface=PromotionSurface.DISCOUNT_CODE,
        coupon_id=uuid4(),
        content=DiscountCodeContent(),
    )
    with pytest.raises(CouponPromotionLinkError):
        await uc.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            actor_user_id=ids["user"],
            payload=payload,
        )


@pytest.mark.asyncio
async def test_create_in_unknown_store_fails(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    from uuid import uuid4

    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Free shipping bar",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
    )
    with pytest.raises(EntityNotFoundError):
        await uc.execute(
            tenant_id=ids["tenant"],
            store_id=uuid4(),
            actor_user_id=ids["user"],
            payload=payload,
        )


@pytest.mark.asyncio
async def test_active_in_future_flips_to_scheduled(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    from datetime import UTC, datetime, timedelta

    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Future auto",
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        starts_at=datetime.now(UTC) + timedelta(days=2),
        discount_rule=DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10),
        content=AutomaticContent(),
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )
    assert out.status == PromotionStatus.SCHEDULED
