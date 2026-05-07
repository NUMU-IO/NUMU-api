"""Resolver — wires fake repos and verifies grouping + priority order."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import (
    DisplayFrequency,
    DisplayTrigger,
    PromotionStatus,
    PromotionSurface,
)
from src.core.services.promotion_eligibility_checker import (
    EligibilityContext,
    PromotionEligibilityChecker,
)
from src.core.services.promotion_resolver import PromotionResolver
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    PopupContent,
)

# ---- Fake in-memory repos ----------------------------------------------------


class _FakePromotionRepo:
    def __init__(self, promotions: list[Promotion]) -> None:
        self.promotions = promotions

    async def list_active_for_storefront(
        self, store_id: UUID, now: datetime
    ) -> list[Promotion]:
        return [
            p
            for p in self.promotions
            if p.store_id == store_id and p.status == PromotionStatus.ACTIVE
        ]

    # Other methods unused by resolver
    async def create(self, p):
        raise NotImplementedError

    async def get_by_id(self, s, p):
        raise NotImplementedError

    async def list_for_store(self, *a, **k):
        raise NotImplementedError

    async def update(self, p):
        raise NotImplementedError

    async def delete(self, s, p):
        raise NotImplementedError


class _FakeDisplayRepo:
    def __init__(self, displays: dict[UUID, list[PromotionDisplay]]) -> None:
        self.displays = displays

    async def list_for_promotion(self, promotion_id: UUID):
        return self.displays.get(promotion_id, [])

    async def replace_for_promotion(self, *a, **k):
        raise NotImplementedError


class _FakeTargetRepo:
    def __init__(self, targets: dict[UUID, list[PromotionTarget]]) -> None:
        self.targets = targets

    async def list_for_promotion(self, promotion_id: UUID):
        return self.targets.get(promotion_id, [])

    async def replace_for_promotion(self, *a, **k):
        raise NotImplementedError


class _FakeDismissalRepo:
    def __init__(self, dismissed: set[UUID] | None = None) -> None:
        self.dismissed = dismissed or set()

    async def record(self, d):
        self.dismissed.add(d.promotion_id)
        return d

    async def list_dismissed_promotion_ids(
        self, store_id, *, customer_id=None, visitor_token=None
    ):
        return self.dismissed


# ---- Tests -------------------------------------------------------------------


def _bar(name: str, store_id: UUID, priority: int = 0) -> Promotion:
    return Promotion(
        tenant_id=uuid4(),
        store_id=store_id,
        name=name,
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        status=PromotionStatus.ACTIVE,
        content=AnnouncementBarContent(),
        priority=priority,
    )


def _popup(name: str, store_id: UUID, priority: int = 0) -> Promotion:
    return Promotion(
        tenant_id=uuid4(),
        store_id=store_id,
        name=name,
        surface=PromotionSurface.POPUP,
        status=PromotionStatus.ACTIVE,
        content=PopupContent(),
        priority=priority,
    )


def _display(promo: Promotion, page_pattern: str = "/") -> PromotionDisplay:
    return PromotionDisplay(
        tenant_id=promo.tenant_id,
        promotion_id=promo.id,
        trigger=DisplayTrigger.ON_LOAD,
        trigger_value={},
        frequency=DisplayFrequency.EVERY_VISIT,
        pages=[page_pattern] if page_pattern != "*" else [],
        device_targets=["desktop", "mobile"],
        is_enabled=True,
    )


@pytest.mark.asyncio
async def test_groups_by_surface():
    store = uuid4()
    bar = _bar("bar", store)
    pop = _popup("pop", store)
    promos = [bar, pop]
    displays = {bar.id: [_display(bar, "*")], pop.id: [_display(pop, "*")]}
    resolver = PromotionResolver(
        promotion_repo=_FakePromotionRepo(promos),
        display_repo=_FakeDisplayRepo(displays),
        target_repo=_FakeTargetRepo({}),
        dismissal_repo=_FakeDismissalRepo(),
        eligibility_checker=PromotionEligibilityChecker(),
    )

    out = await resolver.resolve_active_for_visitor(
        store_id=store, context=EligibilityContext(), page_path="/"
    )
    assert len(out.announcement_bars) == 1
    assert len(out.popups) == 1


@pytest.mark.asyncio
async def test_priority_orders_within_surface():
    store = uuid4()
    low = _bar("low", store, priority=1)
    high = _bar("high", store, priority=10)
    mid = _bar("mid", store, priority=5)
    promos = [low, high, mid]
    displays = {p.id: [_display(p, "*")] for p in promos}
    resolver = PromotionResolver(
        _FakePromotionRepo(promos),
        _FakeDisplayRepo(displays),
        _FakeTargetRepo({}),
        _FakeDismissalRepo(),
        PromotionEligibilityChecker(),
    )

    out = await resolver.resolve_active_for_visitor(
        store_id=store, context=EligibilityContext(), page_path="/"
    )
    names = [r.promotion.name for r in out.announcement_bars]
    assert names == ["high", "mid", "low"]


@pytest.mark.asyncio
async def test_page_filter_drops_non_matching_display():
    store = uuid4()
    bar = _bar("bar", store)
    promos = [bar]
    # Display only valid on /products
    displays = {bar.id: [_display(bar, "/products")]}
    resolver = PromotionResolver(
        _FakePromotionRepo(promos),
        _FakeDisplayRepo(displays),
        _FakeTargetRepo({}),
        _FakeDismissalRepo(),
        PromotionEligibilityChecker(),
    )
    out = await resolver.resolve_active_for_visitor(
        store_id=store, context=EligibilityContext(), page_path="/"
    )
    assert out.announcement_bars == []


@pytest.mark.asyncio
async def test_dismissed_promo_excluded():
    store = uuid4()
    bar = _bar("bar", store)
    promos = [bar]
    displays = {bar.id: [_display(bar, "*")]}
    resolver = PromotionResolver(
        _FakePromotionRepo(promos),
        _FakeDisplayRepo(displays),
        _FakeTargetRepo({}),
        _FakeDismissalRepo(dismissed={bar.id}),
        PromotionEligibilityChecker(),
    )
    out = await resolver.resolve_active_for_visitor(
        store_id=store, context=EligibilityContext(), page_path="/"
    )
    assert out.announcement_bars == []
