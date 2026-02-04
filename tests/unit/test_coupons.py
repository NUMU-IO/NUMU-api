"""Unit tests for coupon entity, CRUD, and apply logic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.entities.coupon import Coupon, CouponType

# =============================================================================
# Coupon Entity Tests
# =============================================================================


class TestCouponEntity:
    """Unit tests for the Coupon domain entity."""

    def _create_coupon(self, **kwargs) -> Coupon:
        """Helper to create a coupon with sensible defaults."""
        defaults = {
            "id": uuid4(),
            "store_id": uuid4(),
            "code": "SAVE10",
            "coupon_type": CouponType.PERCENTAGE,
            "value": Decimal("10"),
            "is_active": True,
        }
        defaults.update(kwargs)
        return Coupon(**defaults)

    def test_create_coupon_with_valid_data(self):
        coupon = self._create_coupon()
        assert coupon.code == "SAVE10"
        assert coupon.coupon_type == CouponType.PERCENTAGE
        assert coupon.value == Decimal("10")
        assert coupon.is_active is True
        assert coupon.usage_count == 0

    def test_calculate_discount_percentage(self):
        coupon = self._create_coupon(
            coupon_type=CouponType.PERCENTAGE, value=Decimal("10")
        )
        assert coupon.calculate_discount(Decimal("10000")) == Decimal(
            "1000"
        )  # 10% of 10000

    def test_calculate_discount_fixed_amount(self):
        coupon = self._create_coupon(coupon_type=CouponType.FIXED, value=Decimal("500"))
        assert coupon.calculate_discount(Decimal("10000")) == Decimal("500")

    def test_calculate_discount_percentage_with_max_cap(self):
        coupon = self._create_coupon(
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("50"),
            max_discount_amount=Decimal("3000"),
        )
        # 50% of 10000 = 5000, capped at 3000
        assert coupon.calculate_discount(Decimal("10000")) == Decimal("3000")

    def test_calculate_discount_fixed_exceeds_subtotal(self):
        coupon = self._create_coupon(
            coupon_type=CouponType.FIXED, value=Decimal("15000")
        )
        # Fixed 15000, but subtotal is only 10000 — capped to subtotal
        assert coupon.calculate_discount(Decimal("10000")) == Decimal("10000")

    def test_calculate_discount_free_shipping(self):
        coupon = self._create_coupon(
            coupon_type=CouponType.FREE_SHIPPING, value=Decimal("0")
        )
        assert coupon.calculate_discount(Decimal("10000")) == Decimal("0")

    def test_is_usable_active_coupon(self):
        coupon = self._create_coupon()
        assert coupon.is_usable is True

    def test_is_usable_inactive_coupon(self):
        coupon = self._create_coupon(is_active=False)
        assert coupon.is_usable is False

    def test_is_expired_coupon(self):
        coupon = self._create_coupon(
            valid_until=datetime.now(UTC) - timedelta(days=1),
        )
        assert coupon.is_expired is True
        assert coupon.is_usable is False

    def test_not_yet_started_coupon(self):
        coupon = self._create_coupon(
            valid_from=datetime.now(UTC) + timedelta(days=1),
        )
        assert coupon.is_started is False
        assert coupon.is_usable is False

    def test_meets_minimum_order(self):
        coupon = self._create_coupon(min_order_amount=Decimal("10000"))
        assert coupon.meets_minimum_order(Decimal("10000")) is True
        assert coupon.meets_minimum_order(Decimal("5000")) is False

    def test_has_remaining_uses_exceeded(self):
        coupon = self._create_coupon(usage_limit=100, usage_count=100)
        assert coupon.has_remaining_uses is False
        assert coupon.is_usable is False

    def test_has_remaining_uses_unlimited(self):
        coupon = self._create_coupon(usage_limit=None)
        assert coupon.has_remaining_uses is True

    def test_record_usage(self):
        coupon = self._create_coupon(usage_count=5, usage_limit=10)
        coupon.record_usage()
        assert coupon.usage_count == 6

    def test_record_usage_at_limit_raises(self):
        coupon = self._create_coupon(usage_count=10, usage_limit=10)
        with pytest.raises(ValueError):
            coupon.record_usage()

    def test_deactivate_and_activate(self):
        coupon = self._create_coupon()
        coupon.deactivate()
        assert coupon.is_active is False
        coupon.activate()
        assert coupon.is_active is True


# =============================================================================
# Apply Coupon Use Case Tests
# =============================================================================


class TestApplyCoupon:
    """Tests for the ApplyCouponUseCase."""

    def _create_coupon_entity(self, **kwargs) -> Coupon:
        defaults = {
            "id": uuid4(),
            "store_id": uuid4(),
            "code": "DISCOUNT20",
            "coupon_type": CouponType.PERCENTAGE,
            "value": Decimal("20"),
            "is_active": True,
        }
        defaults.update(kwargs)
        return Coupon(**defaults)

    def _make_use_case(self, coupon=None):
        from src.application.use_cases.coupons.apply_coupon import ApplyCouponUseCase

        mock_repo = AsyncMock()
        mock_repo.get_by_code = AsyncMock(return_value=coupon)
        mock_repo.increment_usage = AsyncMock()
        return ApplyCouponUseCase(coupon_repository=mock_repo)

    @pytest.mark.asyncio
    async def test_apply_valid_percentage_coupon(self):
        coupon = self._create_coupon_entity(
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("20"),
        )
        use_case = self._make_use_case(coupon=coupon)

        result = await use_case.execute(
            store_id=coupon.store_id,
            code="DISCOUNT20",
            order_amount=Decimal("10000"),
        )
        assert result.discount_amount == Decimal("2000")  # 20% of 10000
        assert result.code == coupon.code

    @pytest.mark.asyncio
    async def test_apply_valid_fixed_coupon(self):
        coupon = self._create_coupon_entity(
            coupon_type=CouponType.FIXED,
            value=Decimal("1500"),
        )
        use_case = self._make_use_case(coupon=coupon)

        result = await use_case.execute(
            store_id=coupon.store_id,
            code="DISCOUNT20",
            order_amount=Decimal("10000"),
        )
        assert result.discount_amount == Decimal("1500")

    @pytest.mark.asyncio
    async def test_apply_free_shipping_coupon(self):
        coupon = self._create_coupon_entity(
            coupon_type=CouponType.FREE_SHIPPING,
            value=Decimal("0"),
        )
        use_case = self._make_use_case(coupon=coupon)

        result = await use_case.execute(
            store_id=coupon.store_id,
            code="DISCOUNT20",
            order_amount=Decimal("10000"),
        )
        assert result.free_shipping is True
        assert result.discount_amount == Decimal("0")

    @pytest.mark.asyncio
    async def test_apply_expired_coupon(self):
        coupon = self._create_coupon_entity(
            valid_until=datetime.now(UTC) - timedelta(days=1),
        )
        use_case = self._make_use_case(coupon=coupon)

        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await use_case.execute(
                store_id=coupon.store_id,
                code="DISCOUNT20",
                order_amount=Decimal("10000"),
            )

    @pytest.mark.asyncio
    async def test_apply_coupon_below_min_order(self):
        coupon = self._create_coupon_entity(min_order_amount=Decimal("20000"))
        use_case = self._make_use_case(coupon=coupon)

        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await use_case.execute(
                store_id=coupon.store_id,
                code="DISCOUNT20",
                order_amount=Decimal("10000"),
            )

    @pytest.mark.asyncio
    async def test_apply_coupon_max_uses_exceeded(self):
        coupon = self._create_coupon_entity(usage_limit=50, usage_count=50)
        use_case = self._make_use_case(coupon=coupon)

        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await use_case.execute(
                store_id=coupon.store_id,
                code="DISCOUNT20",
                order_amount=Decimal("10000"),
            )

    @pytest.mark.asyncio
    async def test_apply_nonexistent_coupon(self):
        use_case = self._make_use_case(coupon=None)

        from src.core.exceptions import EntityNotFoundError

        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                store_id=uuid4(),
                code="DOESNOTEXIST",
                order_amount=Decimal("10000"),
            )
