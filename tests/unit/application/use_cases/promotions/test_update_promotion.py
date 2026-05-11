"""UpdatePromotionUseCase — version conflict + happy path."""

import pytest

from src.application.dto.promotion import (
    CreatePromotionInput,
    UpdatePromotionInput,
)
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.application.use_cases.promotions.update_promotion import (
    UpdatePromotionUseCase,
)
from src.core.enums.promotion_enums import PromotionSurface
from src.core.exceptions.promotion_exceptions import PromotionConflict
from src.core.value_objects.promotion_content import AnnouncementBarContent


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


def _build_update(deps):
    return UpdatePromotionUseCase(
        promotion_repo=deps["promotion_repo"],
        display_repo=deps["display_repo"],
        target_repo=deps["target_repo"],
        translation_repo=deps["translation_repo"],
        coupon_repo=deps["coupon_repo"],
        event_bus=deps["event_bus"],
    )


@pytest.mark.asyncio
async def test_update_renames_and_bumps_version(ids, **fixtures):
    pass


@pytest.mark.asyncio
async def test_update_happy_path(
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
    create_uc = _build_create(deps)
    created = await create_uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=CreatePromotionInput(
            name="V1 name",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            content=AnnouncementBarContent(),
        ),
    )
    update_uc = _build_update(deps)
    updated = await update_uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created.id,
        actor_user_id=ids["user"],
        payload=UpdatePromotionInput(
            version=created.version, name="V2 name", priority=99
        ),
    )
    assert updated.name == "V2 name"
    assert updated.priority == 99
    assert updated.version == created.version + 1


@pytest.mark.asyncio
async def test_update_with_stale_version_raises_conflict(
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
    create_uc = _build_create(deps)
    created = await create_uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=CreatePromotionInput(
            name="V1",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            content=AnnouncementBarContent(),
        ),
    )
    update_uc = _build_update(deps)
    # Apply once
    await update_uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created.id,
        actor_user_id=ids["user"],
        payload=UpdatePromotionInput(version=created.version, name="V2"),
    )
    # Apply again with the original (stale) version
    with pytest.raises(PromotionConflict):
        await update_uc.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            promotion_id=created.id,
            actor_user_id=ids["user"],
            payload=UpdatePromotionInput(version=created.version, name="V3"),
        )
