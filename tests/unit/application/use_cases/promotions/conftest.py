"""Fakes for the promotions use cases — fully in-memory."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.entities.coupon import Coupon, CouponType
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_dismissal import PromotionDismissal
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_event import PromotionEvent
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionStatus
from src.core.events.base import EventBus
from src.core.interfaces.repositories.promotion_event_repository import (
    PromotionEventCounts,
)
from src.core.value_objects.localized_promotion_content import (
    LocalizedPromotionContent,
)

# --------------------------------------------------------------------------- #
# Repos                                                                       #
# --------------------------------------------------------------------------- #


class FakePromotionRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, Promotion] = {}

    async def create(self, promo: Promotion) -> Promotion:
        self.rows[promo.id] = promo
        return promo

    async def get_by_id(self, store_id: UUID, promotion_id: UUID) -> Promotion | None:
        promo = self.rows.get(promotion_id)
        if promo and promo.store_id == store_id:
            return promo
        return None

    async def list_for_store(
        self,
        store_id: UUID,
        *,
        status=None,
        surface=None,
        limit: int = 50,
        offset: int = 0,
    ):
        items = [p for p in self.rows.values() if p.store_id == store_id]
        if status is not None:
            items = [p for p in items if p.status == status]
        if surface is not None:
            items = [p for p in items if p.surface == surface]
        items.sort(key=lambda p: (-p.priority, p.created_at))
        total = len(items)
        return items[offset : offset + limit], total

    async def list_active_for_storefront(
        self,
        store_id: UUID,
        now: datetime,
        *,
        include_drafts: bool = False,
    ) -> list[Promotion]:
        previewable = {
            PromotionStatus.ACTIVE,
            PromotionStatus.DRAFT,
            PromotionStatus.SCHEDULED,
            PromotionStatus.PAUSED,
        }
        return [
            p
            for p in self.rows.values()
            if p.store_id == store_id
            and (
                (include_drafts and p.status in previewable)
                or (not include_drafts and p.status == PromotionStatus.ACTIVE)
            )
        ]

    async def update(self, promo: Promotion) -> Promotion:
        self.rows[promo.id] = promo
        return promo

    async def delete(self, store_id: UUID, promotion_id: UUID) -> None:
        if promotion_id in self.rows and self.rows[promotion_id].store_id == store_id:
            del self.rows[promotion_id]


class FakeDisplayRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, list[PromotionDisplay]] = defaultdict(list)

    async def list_for_promotion(self, promotion_id: UUID):
        return list(self.rows.get(promotion_id, []))

    async def replace_for_promotion(self, promotion_id, displays):
        self.rows[promotion_id] = list(displays)
        return list(displays)


class FakeTargetRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, list[PromotionTarget]] = defaultdict(list)

    async def list_for_promotion(self, promotion_id: UUID):
        return list(self.rows.get(promotion_id, []))

    async def replace_for_promotion(self, promotion_id, targets):
        self.rows[promotion_id] = list(targets)
        return list(targets)


class FakeTranslationRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, LocalizedPromotionContent]] = {}

    async def get_for_promotion(self, promotion_id: UUID):
        return dict(self.rows.get(promotion_id, {}))

    async def replace_for_promotion(self, promotion_id, tenant_id, translations):
        self.rows[promotion_id] = dict(translations)


class FakeDismissalRepo:
    def __init__(self) -> None:
        self.rows: list[PromotionDismissal] = []

    async def record(self, d):
        # idempotent on (promotion_id, customer_id) / (promotion_id, visitor_token)
        for existing in self.rows:
            if existing.promotion_id == d.promotion_id and (
                (existing.customer_id and existing.customer_id == d.customer_id)
                or (
                    existing.visitor_token and existing.visitor_token == d.visitor_token
                )
            ):
                return existing
        self.rows.append(d)
        return d

    async def list_dismissed_promotion_ids(
        self, store_id, *, customer_id=None, visitor_token=None
    ):
        out: set[UUID] = set()
        for r in self.rows:
            if customer_id is not None and r.customer_id == customer_id:
                out.add(r.promotion_id)
            if visitor_token is not None and r.visitor_token == visitor_token:
                out.add(r.promotion_id)
        return out


class FakeEventRepo:
    def __init__(self) -> None:
        self.rows: list[PromotionEvent] = []

    async def record(self, event: PromotionEvent) -> None:
        self.rows.append(event)

    async def record_many(self, events):
        self.rows.extend(events)

    async def counts_for_promotion(
        self, promotion_id: UUID, *, since=None, until=None
    ) -> PromotionEventCounts:
        rows = [e for e in self.rows if e.promotion_id == promotion_id]
        return PromotionEventCounts(
            promotion_id=promotion_id,
            impressions=sum(1 for e in rows if e.event_type == "impression"),
            clicks=sum(1 for e in rows if e.event_type == "click"),
            dismissals=sum(1 for e in rows if e.event_type == "dismiss"),
            redemptions=sum(1 for e in rows if e.event_type == "redeem"),
            conversions=sum(1 for e in rows if e.event_type == "convert"),
            revenue_cents=sum(e.discount_amount_cents or 0 for e in rows),
        )

    async def counts_for_store(
        self, store_id, *, since=None, until=None, event_types=None
    ):
        out: dict[UUID, PromotionEventCounts] = {}
        ids = {e.promotion_id for e in self.rows if e.store_id == store_id}
        for pid in ids:
            out[pid] = await self.counts_for_promotion(pid)
        return out


class FakeCouponRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, Coupon] = {}

    async def get_by_id(self, coupon_id: UUID):
        return self.rows.get(coupon_id)

    async def get_by_code(self, store_id: UUID, code: str):
        normalized = code.strip().upper()
        for c in self.rows.values():
            if c.store_id == store_id and c.code == normalized:
                return c
        return None

    async def get_by_store(self, *a, **k):
        raise NotImplementedError

    async def count_by_store(self, *a, **k):
        raise NotImplementedError

    async def get_active_by_store(self, *a, **k):
        raise NotImplementedError

    async def list_with_filters(self, *a, **k):
        raise NotImplementedError

    async def count_with_filters(self, *a, **k):
        raise NotImplementedError

    async def increment_usage(self, coupon_id: UUID):
        c = self.rows.get(coupon_id)
        if c:
            c.usage_count += 1

    async def get_all(self, **k):
        return list(self.rows.values())

    async def create(self, coupon: Coupon) -> Coupon:
        self.rows[coupon.id] = coupon
        return coupon

    async def update(self, coupon: Coupon) -> Coupon:
        self.rows[coupon.id] = coupon
        return coupon

    async def delete(self, coupon_id: UUID) -> bool:
        return self.rows.pop(coupon_id, None) is not None

    async def count(self) -> int:
        return len(self.rows)


class FakeStoreRepo:
    """Just-enough store stub for use cases that only check tenant ownership."""

    def __init__(self, store_id: UUID, tenant_id: UUID) -> None:
        self._store = type("S", (), {"id": store_id, "tenant_id": tenant_id})()

    async def get_by_id(self, store_id: UUID):
        return self._store if self._store.id == store_id else None

    async def get_all(self, *a, **k):
        return []

    async def create(self, *a, **k):
        raise NotImplementedError

    async def update(self, *a, **k):
        raise NotImplementedError

    async def delete(self, *a, **k):
        raise NotImplementedError

    async def count(self, *a, **k):
        return 1


# --------------------------------------------------------------------------- #
# Pytest fixtures                                                             #
# --------------------------------------------------------------------------- #


@pytest.fixture
def ids():
    return {"tenant": uuid4(), "store": uuid4(), "user": uuid4()}


@pytest.fixture
def promotion_repo() -> FakePromotionRepo:
    return FakePromotionRepo()


@pytest.fixture
def display_repo() -> FakeDisplayRepo:
    return FakeDisplayRepo()


@pytest.fixture
def target_repo() -> FakeTargetRepo:
    return FakeTargetRepo()


@pytest.fixture
def translation_repo() -> FakeTranslationRepo:
    return FakeTranslationRepo()


@pytest.fixture
def dismissal_repo() -> FakeDismissalRepo:
    return FakeDismissalRepo()


@pytest.fixture
def event_repo() -> FakeEventRepo:
    return FakeEventRepo()


@pytest.fixture
def coupon_repo() -> FakeCouponRepo:
    return FakeCouponRepo()


@pytest.fixture
def store_repo(ids) -> FakeStoreRepo:
    return FakeStoreRepo(ids["store"], ids["tenant"])


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def make_coupon(ids, coupon_repo):
    async def _make(code: str = "WELCOME10", value: Decimal = Decimal("10")):
        coupon = Coupon(
            id=uuid4(),
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            code=code,
            coupon_type=CouponType.PERCENTAGE,
            value=value,
            is_active=True,
            usage_count=0,
        )
        await coupon_repo.create(coupon)
        return coupon

    return _make
