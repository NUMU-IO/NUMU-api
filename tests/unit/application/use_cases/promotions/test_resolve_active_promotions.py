"""ResolveActivePromotionsUseCase — grouping by surface."""

import pytest

from src.application.dto.promotion_resolution import VisitorContextInput
from src.application.use_cases.promotions.resolve_active_promotions import (
    ResolveActivePromotionsUseCase,
)
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.enums.promotion_enums import (
    DisplayFrequency,
    DisplayTrigger,
    PromotionStatus,
    PromotionSurface,
)
from src.core.services.promotion_eligibility_checker import (
    PromotionEligibilityChecker,
)
from src.core.services.promotion_resolver import PromotionResolver
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    PopupContent,
)


def _bar(store_id, tenant_id, name="bar", priority=0):
    return Promotion(
        tenant_id=tenant_id,
        store_id=store_id,
        name=name,
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        status=PromotionStatus.ACTIVE,
        content=AnnouncementBarContent(),
        priority=priority,
    )


def _popup(store_id, tenant_id, name="pop", priority=0):
    return Promotion(
        tenant_id=tenant_id,
        store_id=store_id,
        name=name,
        surface=PromotionSurface.POPUP,
        status=PromotionStatus.ACTIVE,
        content=PopupContent(),
        priority=priority,
    )


def _display(promo: Promotion):
    return PromotionDisplay(
        tenant_id=promo.tenant_id,
        promotion_id=promo.id,
        trigger=DisplayTrigger.ON_LOAD,
        trigger_value={},
        frequency=DisplayFrequency.EVERY_VISIT,
        pages=[],
        device_targets=["desktop", "mobile"],
        is_enabled=True,
    )


@pytest.mark.asyncio
async def test_groups_by_surface_and_emits_fingerprint(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    dismissal_repo,
    coupon_repo,
):
    bar = _bar(ids["store"], ids["tenant"], priority=10)
    pop = _popup(ids["store"], ids["tenant"], priority=5)
    await promotion_repo.create(bar)
    await promotion_repo.create(pop)
    await display_repo.replace_for_promotion(bar.id, [_display(bar)])
    await display_repo.replace_for_promotion(pop.id, [_display(pop)])

    resolver = PromotionResolver(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        dismissal_repo=dismissal_repo,
        eligibility_checker=PromotionEligibilityChecker(),
    )
    uc = ResolveActivePromotionsUseCase(resolver=resolver, coupon_repo=coupon_repo)

    out = await uc.execute(
        store_id=ids["store"],
        tenant_id=ids["tenant"],
        visitor=VisitorContextInput(page_path="/", locale="ar"),
    )

    assert len(out.announcement_bars) == 1
    assert len(out.popups) == 1
    assert out.announcement_bars[0].fingerprint
    assert out.announcement_bars[0].promotion_id == bar.id
    assert out.popups[0].promotion_id == pop.id
