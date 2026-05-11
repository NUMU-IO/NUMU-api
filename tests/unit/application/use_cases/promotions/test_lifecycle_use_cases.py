"""Activate / pause / archive / delete / list / get / duplicate."""

import pytest

from src.application.dto.promotion import CreatePromotionInput
from src.application.use_cases.promotions.activate_promotion import (
    ActivatePromotionUseCase,
    ArchivePromotionUseCase,
    PausePromotionUseCase,
)
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.application.use_cases.promotions.delete_promotion import (
    DeletePromotionUseCase,
)
from src.application.use_cases.promotions.duplicate_promotion import (
    DuplicatePromotionUseCase,
)
from src.application.use_cases.promotions.get_promotion import GetPromotionUseCase
from src.application.use_cases.promotions.list_promotions import (
    ListPromotionsUseCase,
)
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    PromotionNotFound,
    PromotionStateError,
)
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    DiscountCodeContent,
)


def _build_create(deps):
    return CreatePromotionUseCase(
        promotion_repo=deps["promotion_repo"],
        display_repo=deps["display_repo"],
        target_repo=deps["target_repo"],
        translation_repo=deps["translation_repo"],
        coupon_repo=deps["coupon_repo"],
        store_repo=deps["store_repo"],
        event_bus=deps["event_bus"],
    )


def _life_kwargs(deps):
    return {
        "promotion_repo": deps["promotion_repo"],
        "display_repo": deps["display_repo"],
        "target_repo": deps["target_repo"],
        "event_bus": deps["event_bus"],
    }


@pytest.fixture
async def created_promo(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    deps = locals()
    uc = _build_create(deps)
    return await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=CreatePromotionInput(
            name="Bar",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            content=AnnouncementBarContent(),
        ),
    )


@pytest.mark.asyncio
async def test_activate_then_pause_then_archive(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    created_promo,
):
    deps = locals()
    activate = ActivatePromotionUseCase(**_life_kwargs(deps))
    out = await activate.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created_promo.id,
        actor_user_id=ids["user"],
    )
    assert out.status == PromotionStatus.ACTIVE
    pause = PausePromotionUseCase(**_life_kwargs(deps))
    out = await pause.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created_promo.id,
        actor_user_id=ids["user"],
    )
    assert out.status == PromotionStatus.PAUSED
    archive = ArchivePromotionUseCase(**_life_kwargs(deps))
    out = await archive.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created_promo.id,
        actor_user_id=ids["user"],
    )
    assert out.status == PromotionStatus.ARCHIVED


@pytest.mark.asyncio
async def test_activate_archived_raises(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    created_promo,
):
    deps = locals()
    archive = ArchivePromotionUseCase(**_life_kwargs(deps))
    await archive.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created_promo.id,
        actor_user_id=ids["user"],
    )
    activate = ActivatePromotionUseCase(**_life_kwargs(deps))
    with pytest.raises(PromotionStateError):
        await activate.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            promotion_id=created_promo.id,
            actor_user_id=ids["user"],
        )


@pytest.mark.asyncio
async def test_get_and_list(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    created_promo,
):
    deps = locals()
    get_uc = GetPromotionUseCase(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
    )
    one = await get_uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created_promo.id,
    )
    assert one.id == created_promo.id

    list_uc = ListPromotionsUseCase(promotion_repo=promotion_repo)
    page = await list_uc.execute(store_id=ids["store"])
    assert page.total == 1
    assert page.items[0].id == created_promo.id


@pytest.mark.asyncio
async def test_delete(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    created_promo,
):
    delete_uc = DeletePromotionUseCase(
        promotion_repo=promotion_repo, event_bus=event_bus
    )
    await delete_uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created_promo.id,
        actor_user_id=ids["user"],
    )
    assert created_promo.id not in promotion_repo.rows
    with pytest.raises(PromotionNotFound):
        await delete_uc.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            promotion_id=created_promo.id,
            actor_user_id=ids["user"],
        )


@pytest.mark.asyncio
async def test_duplicate_announcement_bar(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    created_promo,
):
    dup = DuplicatePromotionUseCase(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        event_bus=event_bus,
    )
    out = await dup.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created_promo.id,
        actor_user_id=ids["user"],
        locale="en",
    )
    assert out.id != created_promo.id
    assert out.status == PromotionStatus.DRAFT
    assert "(copy)" in out.name


@pytest.mark.asyncio
async def test_duplicate_discount_code_rejected(
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
    deps = locals()
    create = _build_create(deps)
    code_promo = await create.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=CreatePromotionInput(
            name="Code",
            surface=PromotionSurface.DISCOUNT_CODE,
            coupon_id=coupon.id,
            content=DiscountCodeContent(),
        ),
    )
    dup = DuplicatePromotionUseCase(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        event_bus=event_bus,
    )
    with pytest.raises(CouponPromotionLinkError):
        await dup.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            promotion_id=code_promo.id,
            actor_user_id=ids["user"],
        )
