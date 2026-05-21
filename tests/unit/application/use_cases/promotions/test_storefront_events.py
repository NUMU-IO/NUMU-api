"""Storefront-side use cases: record event + dismiss."""

from uuid import uuid4

import pytest

from src.application.dto.promotion import CreatePromotionInput
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.application.use_cases.promotions.dismiss_promotion import (
    DismissPromotionUseCase,
)
from src.application.use_cases.promotions.record_promotion_event import (
    RecordPromotionEventUseCase,
)
from src.core.enums.promotion_enums import PromotionEventType, PromotionSurface
from src.core.exceptions.promotion_exceptions import PromotionNotFound
from src.core.value_objects.promotion_content import AnnouncementBarContent


@pytest.mark.asyncio
async def test_record_event_writes_to_repo(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    event_repo,
):
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
            name="Bar",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            content=AnnouncementBarContent(),
        ),
    )
    rec = RecordPromotionEventUseCase(
        promotion_repo=promotion_repo, event_repo=event_repo
    )
    await rec.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=promo.id,
        event_type=PromotionEventType.IMPRESSION,
        session_id="anon-1",
    )
    assert len(event_repo.rows) == 1
    assert event_repo.rows[0].event_type == "impression"


@pytest.mark.asyncio
async def test_record_event_unknown_promo_raises(ids, promotion_repo, event_repo):
    rec = RecordPromotionEventUseCase(
        promotion_repo=promotion_repo, event_repo=event_repo
    )
    with pytest.raises(PromotionNotFound):
        await rec.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            promotion_id=uuid4(),
            event_type=PromotionEventType.IMPRESSION,
        )


@pytest.mark.asyncio
async def test_dismiss_writes_dismissal_and_event(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    event_repo,
    dismissal_repo,
):
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
            name="Bar",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            content=AnnouncementBarContent(),
        ),
    )
    dismiss = DismissPromotionUseCase(
        promotion_repo=promotion_repo,
        dismissal_repo=dismissal_repo,
        event_repo=event_repo,
    )
    await dismiss.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=promo.id,
        visitor_token="anon-7",
    )
    assert len(dismissal_repo.rows) == 1
    assert any(e.event_type == "dismiss" for e in event_repo.rows)


@pytest.mark.asyncio
async def test_dismiss_requires_subject(
    ids, promotion_repo, dismissal_repo, event_repo
):
    dismiss = DismissPromotionUseCase(
        promotion_repo=promotion_repo,
        dismissal_repo=dismissal_repo,
        event_repo=event_repo,
    )
    with pytest.raises(ValueError):
        await dismiss.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            promotion_id=uuid4(),
        )
