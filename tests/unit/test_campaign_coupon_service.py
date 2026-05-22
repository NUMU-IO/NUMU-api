"""Unit tests for campaign_coupon_service pure functions.

The DB-dependent ``generate_unique_code`` is exercised via the
integration suite; this file covers the pure-Python helpers:

* ``_slugify_for_coupon`` — uppercase ASCII slug, length cap, Arabic
  fallback
* ``_random_suffix`` — alphabet + length
* ``build_campaign_coupon`` — coupon construction + validation
  (percentage > 100 rejected, fixed <=0 rejected, campaign_id wired)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.application.services.campaign_coupon_service import (
    _CROCKFORD_ALPHABET,
    _SUFFIX_LENGTH,
    _random_suffix,
    _slugify_for_coupon,
    build_campaign_coupon,
)
from src.core.entities.coupon import CouponType
from src.core.entities.marketing_campaign import (
    CampaignChannel,
    CampaignStatus,
    MarketingCampaign,
)


def _make_campaign(name: str) -> MarketingCampaign:
    return MarketingCampaign(
        id=uuid4(),
        tenant_id=uuid4(),
        store_id=uuid4(),
        channel=CampaignChannel.EMAIL,
        name=name,
        status=CampaignStatus.DRAFT,
        short_code="AB7K9X",
        created_by=uuid4(),
    )


class TestSlugifyForCoupon:
    def test_basic_ascii(self):
        assert _slugify_for_coupon("Eid Sale 2026") == "EID-SALE-2026"

    def test_uppercases(self):
        assert _slugify_for_coupon("eid sale") == "EID-SALE"

    def test_strips_special_chars(self):
        assert _slugify_for_coupon("Eid Sale!! 50% Off!") == "EID-SALE-50-OFF"

    def test_collapses_multiple_separators(self):
        assert _slugify_for_coupon("Eid    Sale___2026") == "EID-SALE-2026"

    def test_strips_leading_trailing_dashes(self):
        # Spaces at the ends should not produce leading/trailing dashes
        # — those would render ugly in the final ``-SUFFIX`` code.
        assert _slugify_for_coupon("  Eid Sale  ").startswith("EID")
        assert not _slugify_for_coupon("  Eid Sale  ").endswith("-")

    def test_arabic_falls_back_to_campaign(self):
        # NUMU's primary market is Arabic; campaign names like "تخفيضات
        # العيد" must produce a readable prefix even when the name has
        # zero ASCII characters.
        assert _slugify_for_coupon("تخفيضات العيد") == "CAMPAIGN"

    def test_empty_string_falls_back(self):
        assert _slugify_for_coupon("") == "CAMPAIGN"

    def test_only_special_chars_falls_back(self):
        assert _slugify_for_coupon("!!!@@@###") == "CAMPAIGN"

    def test_truncates_long_names(self):
        # Cap is 32 chars on the slug so the final code (slug + dash +
        # 6-char suffix) fits within the coupons.code VARCHAR(50).
        long_name = "A" * 100
        slug = _slugify_for_coupon(long_name)
        assert len(slug) <= 32


class TestRandomSuffix:
    def test_correct_length(self):
        assert len(_random_suffix()) == _SUFFIX_LENGTH == 6

    def test_uses_crockford_alphabet(self):
        for _ in range(100):
            suffix = _random_suffix()
            for ch in suffix:
                assert ch in _CROCKFORD_ALPHABET

    def test_no_forbidden_chars(self):
        forbidden = {"I", "L", "O", "U"}
        for _ in range(200):
            suffix = _random_suffix()
            assert not (set(suffix) & forbidden)

    def test_suffixes_vary(self):
        codes = {_random_suffix() for _ in range(100)}
        # 32^6 namespace; 100 draws colliding is astronomically unlikely
        # unless RNG is broken.
        assert len(codes) > 95


class TestBuildCampaignCoupon:
    def test_percentage_coupon(self):
        campaign = _make_campaign("Eid Sale 2026")
        coupon = build_campaign_coupon(
            store_id=campaign.store_id,
            tenant_id=campaign.tenant_id,
            campaign=campaign,
            code="EID-SALE-2026-AB7K9X",
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("20"),
        )
        assert coupon.code == "EID-SALE-2026-AB7K9X"
        assert coupon.coupon_type == CouponType.PERCENTAGE
        assert coupon.value == Decimal("20")
        assert coupon.campaign_id == campaign.id
        assert coupon.is_active is True

    def test_fixed_coupon(self):
        campaign = _make_campaign("Eid Sale")
        coupon = build_campaign_coupon(
            store_id=campaign.store_id,
            tenant_id=campaign.tenant_id,
            campaign=campaign,
            code="EID-SALE-AB7K9X",
            coupon_type=CouponType.FIXED,
            value=Decimal("50"),
        )
        assert coupon.coupon_type == CouponType.FIXED
        assert coupon.value == Decimal("50")

    def test_rejects_percentage_over_100(self):
        # 100% is the maximum sane percentage discount (free).
        # 150% would refund money to the customer — almost always a
        # merchant typo.
        campaign = _make_campaign("Crazy Sale")
        with pytest.raises(ValueError, match="cannot exceed 100"):
            build_campaign_coupon(
                store_id=campaign.store_id,
                tenant_id=campaign.tenant_id,
                campaign=campaign,
                code="CRAZY-SALE-AB7K9X",
                coupon_type=CouponType.PERCENTAGE,
                value=Decimal("150"),
            )

    def test_accepts_percentage_100_exactly(self):
        # 100% means free — legitimate use case (giveaways, full-comp
        # codes for influencers). Must not be rejected.
        campaign = _make_campaign("Giveaway")
        coupon = build_campaign_coupon(
            store_id=campaign.store_id,
            tenant_id=campaign.tenant_id,
            campaign=campaign,
            code="GIVEAWAY-AB7K9X",
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("100"),
        )
        assert coupon.value == Decimal("100")

    def test_rejects_zero_fixed_value(self):
        # A 0 EGP fixed-amount coupon is meaningless — likely a
        # merchant mistake selecting "fixed" when they meant
        # "percentage 0" (also wrong, but a different mistake).
        campaign = _make_campaign("Bug Sale")
        with pytest.raises(ValueError, match="must be positive"):
            build_campaign_coupon(
                store_id=campaign.store_id,
                tenant_id=campaign.tenant_id,
                campaign=campaign,
                code="BUG-AB7K9X",
                coupon_type=CouponType.FIXED,
                value=Decimal("0"),
            )

    def test_rejects_negative_fixed_value(self):
        campaign = _make_campaign("Bug Sale")
        # Negative values are filtered by the underlying ``Coupon``
        # entity's ``validate_value``; ours catches it earlier at the
        # campaign-issuance boundary so the error message is more
        # specific.
        with pytest.raises(ValueError):
            build_campaign_coupon(
                store_id=campaign.store_id,
                tenant_id=campaign.tenant_id,
                campaign=campaign,
                code="BUG-AB7K9X",
                coupon_type=CouponType.FIXED,
                value=Decimal("-5"),
            )

    def test_wires_optional_fields(self):
        campaign = _make_campaign("Eid Sale")
        valid_from = datetime(2026, 5, 1, tzinfo=UTC)
        valid_until = valid_from + timedelta(days=14)
        coupon = build_campaign_coupon(
            store_id=campaign.store_id,
            tenant_id=campaign.tenant_id,
            campaign=campaign,
            code="EID-SALE-AB7K9X",
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("15"),
            min_order_amount=Decimal("100"),
            max_discount_amount=Decimal("50"),
            usage_limit=500,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        assert coupon.min_order_amount == Decimal("100")
        assert coupon.max_discount_amount == Decimal("50")
        assert coupon.usage_limit == 500
        assert coupon.valid_from == valid_from
        assert coupon.valid_until == valid_until

    def test_arabic_campaign_name_works(self):
        # Even though the slug becomes "CAMPAIGN", the build should
        # succeed and the coupon is still usable. The slug uniqueness
        # is per-store, and the random suffix carries the entropy.
        campaign = _make_campaign("تخفيضات العيد")
        coupon = build_campaign_coupon(
            store_id=campaign.store_id,
            tenant_id=campaign.tenant_id,
            campaign=campaign,
            code="CAMPAIGN-AB7K9X",
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("10"),
        )
        assert coupon.campaign_id == campaign.id


class TestCampaignAttributionFallback:
    """The Coupon entity now carries an optional campaign_id.

    These tests guard the contract that ``Coupon(campaign_id=...)`` is
    serializable and round-trips through the Pydantic model unchanged
    — important because the checkout reads this field on the value
    object after the use case returns.
    """

    def test_campaign_id_defaults_to_none(self):
        from src.core.entities.coupon import Coupon

        coupon = Coupon(
            store_id=uuid4(),
            code="STANDALONE",
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("10"),
        )
        assert coupon.campaign_id is None

    def test_campaign_id_round_trips(self):
        from src.core.entities.coupon import Coupon

        campaign_id = uuid4()
        coupon = Coupon(
            store_id=uuid4(),
            code="EID",
            coupon_type=CouponType.FIXED,
            value=Decimal("25"),
            campaign_id=campaign_id,
        )
        dumped = coupon.model_dump()
        assert dumped["campaign_id"] == campaign_id
