"""Invariants and lifecycle behavior of the Promotion aggregate root."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_dismissal import PromotionDismissal
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    PromotionStateError,
)
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    AutomaticContent,
    DiscountCodeContent,
)


@pytest.fixture
def ids():
    return {"tenant": uuid4(), "store": uuid4(), "coupon": uuid4()}


def test_discount_code_requires_coupon_id(ids):
    with pytest.raises(CouponPromotionLinkError):
        Promotion(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            name="bad",
            surface=PromotionSurface.DISCOUNT_CODE,
            content=DiscountCodeContent(),
        )


def test_non_code_promotion_rejects_coupon_id(ids):
    with pytest.raises(CouponPromotionLinkError):
        Promotion(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            name="bad",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            coupon_id=ids["coupon"],
            content=AnnouncementBarContent(),
        )


def test_content_surface_must_match_parent(ids):
    with pytest.raises(ValueError, match="content.surface"):
        Promotion(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            name="bad",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            content=AutomaticContent(),
        )


def test_ends_at_must_be_after_starts_at(ids):
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="ends_at"):
        Promotion(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            name="bad",
            surface=PromotionSurface.ANNOUNCEMENT_BAR,
            content=AnnouncementBarContent(),
            starts_at=now,
            ends_at=now,
        )


def test_activate_from_draft_paused_scheduled(ids):
    p = Promotion(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name="ok",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
    )
    assert p.status == PromotionStatus.DRAFT
    p.activate()
    assert p.status == PromotionStatus.ACTIVE
    p.pause()
    assert p.status == PromotionStatus.PAUSED
    p.activate()
    assert p.status == PromotionStatus.ACTIVE


def test_activate_from_archived_rejected(ids):
    p = Promotion(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name="ok",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
    )
    p.archive()
    with pytest.raises(PromotionStateError):
        p.activate()


def test_schedule_sets_window_and_status(ids):
    p = Promotion(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name="ok",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
    )
    starts = datetime.now(UTC) + timedelta(days=1)
    ends = starts + timedelta(days=7)
    p.schedule(starts, ends)
    assert p.status == PromotionStatus.SCHEDULED
    assert p.starts_at == starts
    assert p.ends_at == ends


def test_is_currently_active_respects_window(ids):
    now = datetime.now(UTC)
    p = Promotion(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name="ok",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
    )
    p.activate()
    assert p.is_currently_active is True


def test_is_currently_active_false_before_window(ids):
    now = datetime.now(UTC)
    p = Promotion(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name="ok",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
        starts_at=now + timedelta(hours=1),
    )
    p.activate()
    assert p.is_currently_active is False


def test_expire_if_window_passed(ids):
    p = Promotion(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name="ok",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
        ends_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    p.activate()
    changed = p.expire_if_window_passed()
    assert changed is True
    assert p.status == PromotionStatus.EXPIRED


def test_dismissal_requires_exactly_one_subject():
    with pytest.raises(ValueError, match="exactly one"):
        PromotionDismissal(tenant_id=uuid4(), promotion_id=uuid4())
    with pytest.raises(ValueError, match="exactly one"):
        PromotionDismissal(
            tenant_id=uuid4(),
            promotion_id=uuid4(),
            customer_id=uuid4(),
            visitor_token="tok",
        )
    # OK shapes
    PromotionDismissal(tenant_id=uuid4(), promotion_id=uuid4(), customer_id=uuid4())
    PromotionDismissal(tenant_id=uuid4(), promotion_id=uuid4(), visitor_token="anon-1")


def test_discount_rule_kind_required_fields():
    # PERCENTAGE without value_percent → fails
    with pytest.raises(ValueError):
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE)
    # FIXED without value_cents → fails
    with pytest.raises(ValueError):
        DiscountRule(kind=DiscountRuleKind.FIXED)
    # BOGO without buy/get → fails
    with pytest.raises(ValueError):
        DiscountRule(kind=DiscountRuleKind.BOGO)
    # TIERED without tiers → fails
    with pytest.raises(ValueError):
        DiscountRule(kind=DiscountRuleKind.TIERED)
    # FREE_SHIPPING with no extra fields → ok
    DiscountRule(kind=DiscountRuleKind.FREE_SHIPPING)
