"""Eligibility checking — every target kind, schedule, dismissal."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import (
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.services.promotion_eligibility_checker import (
    EligibilityContext,
    PromotionEligibilityChecker,
)
from src.core.value_objects.promotion_content import AnnouncementBarContent


def _promo(**overrides) -> Promotion:
    base = {
        "tenant_id": uuid4(),
        "store_id": uuid4(),
        "name": "ok",
        "surface": PromotionSurface.ANNOUNCEMENT_BAR,
        "status": PromotionStatus.ACTIVE,
        "content": AnnouncementBarContent(),
    }
    base.update(overrides)
    return Promotion(**base)


def test_inactive_promotion_is_not_eligible():
    p = _promo(status=PromotionStatus.PAUSED)
    res = PromotionEligibilityChecker().is_eligible(p, [], EligibilityContext())
    assert not res.eligible


def test_window_in_future_blocks():
    p = _promo(starts_at=datetime.now(UTC) + timedelta(hours=1))
    res = PromotionEligibilityChecker().is_eligible(p, [], EligibilityContext())
    assert not res.eligible


def test_window_passed_blocks():
    p = _promo(ends_at=datetime.now(UTC) - timedelta(seconds=1))
    res = PromotionEligibilityChecker().is_eligible(p, [], EligibilityContext())
    assert not res.eligible


def test_dismissed_short_circuits():
    p = _promo()
    ctx = EligibilityContext(dismissed_promotion_ids={p.id})
    res = PromotionEligibilityChecker().is_eligible(p, [], ctx)
    assert not res.eligible
    assert any("dismissed" in r for r in res.reasons)


def test_audience_new_visitor_match():
    p = _promo()
    target = PromotionTarget(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        target_kind=TargetKind.AUDIENCE,
        target_value={"kind": "new_visitor"},
    )
    yes = EligibilityContext(is_first_visit=True)
    no = EligibilityContext(is_first_visit=False)
    chk = PromotionEligibilityChecker()
    assert chk.is_eligible(p, [target], yes).eligible
    assert not chk.is_eligible(p, [target], no).eligible


def test_audience_logged_in_excludes_guests():
    p = _promo()
    target = PromotionTarget(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        target_kind=TargetKind.AUDIENCE,
        target_value={"kind": "logged_in"},
    )
    chk = PromotionEligibilityChecker()
    assert chk.is_eligible(p, [target], EligibilityContext(is_logged_in=True)).eligible
    assert not chk.is_eligible(
        p, [target], EligibilityContext(is_logged_in=False)
    ).eligible


def test_product_target_matches_when_in_cart():
    p = _promo()
    pid = uuid4()
    target = PromotionTarget(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        target_kind=TargetKind.PRODUCT,
        target_value={"product_ids": [str(pid)]},
    )
    chk = PromotionEligibilityChecker()
    yes = EligibilityContext(cart_product_ids=[pid])
    no = EligibilityContext(cart_product_ids=[uuid4()])
    assert chk.is_eligible(p, [target], yes).eligible
    assert not chk.is_eligible(p, [target], no).eligible


def test_geo_country_filter():
    p = _promo()
    target = PromotionTarget(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        target_kind=TargetKind.GEO,
        target_value={"countries": ["EG"]},
    )
    chk = PromotionEligibilityChecker()
    assert chk.is_eligible(p, [target], EligibilityContext(country="EG")).eligible
    assert not chk.is_eligible(p, [target], EligibilityContext(country="SA")).eligible


def test_customer_tag_match():
    p = _promo()
    target = PromotionTarget(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        target_kind=TargetKind.CUSTOMER_TAG,
        target_value={"tags": ["vip"]},
    )
    chk = PromotionEligibilityChecker()
    assert chk.is_eligible(
        p, [target], EligibilityContext(customer_tags=["vip"])
    ).eligible
    assert not chk.is_eligible(
        p, [target], EligibilityContext(customer_tags=["bronze"])
    ).eligible


def test_exclusion_target_blocks_when_match():
    p = _promo()
    pid = uuid4()
    target = PromotionTarget(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        target_kind=TargetKind.PRODUCT,
        target_value={"product_ids": [str(pid)]},
        inclusion=False,
    )
    chk = PromotionEligibilityChecker()
    assert not chk.is_eligible(
        p, [target], EligibilityContext(cart_product_ids=[pid])
    ).eligible
    assert chk.is_eligible(
        p, [target], EligibilityContext(cart_product_ids=[uuid4()])
    ).eligible


# -------- Phase B: usage caps + role-tagged target skipping ------------------


def test_usage_limit_total_blocks_when_reached():
    p = _promo(usage_limit_total=5)
    chk = PromotionEligibilityChecker()
    # Under the cap → eligible.
    assert chk.is_eligible(p, [], EligibilityContext(convert_count_total=4)).eligible
    # At the cap → blocked.
    assert not chk.is_eligible(
        p, [], EligibilityContext(convert_count_total=5)
    ).eligible


def test_usage_limit_per_customer_only_applies_when_known():
    p = _promo(usage_limit_per_customer=2)
    chk = PromotionEligibilityChecker()
    # Anonymous visitor (no customer_id) → per-customer cap is skipped,
    # only the total cap matters; with no total cap set, it's eligible.
    assert chk.is_eligible(
        p, [], EligibilityContext(convert_count_per_customer=10)
    ).eligible
    # Logged-in visitor at the cap → blocked.
    cid = uuid4()
    assert not chk.is_eligible(
        p,
        [],
        EligibilityContext(customer_id=cid, convert_count_per_customer=2),
    ).eligible


def test_role_tagged_targets_are_skipped_for_eligibility():
    # A role-tagged target (buy_set / get_set) feeds the BOGO discount
    # calculator only — the eligibility checker must not gate on it.
    p = _promo()
    pid_in_cart = uuid4()
    pid_in_buy_set = uuid4()
    role_target = PromotionTarget(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        target_kind=TargetKind.PRODUCT,
        target_value={"product_ids": [str(pid_in_buy_set)]},
        inclusion=True,
        role="buy_set",
    )
    chk = PromotionEligibilityChecker()
    # Cart doesn't contain the buy_set product, but eligibility should
    # still pass because role-tagged targets aren't eligibility rules.
    res = chk.is_eligible(
        p,
        [role_target],
        EligibilityContext(cart_product_ids=[pid_in_cart]),
    )
    assert res.eligible
