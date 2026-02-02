"""Unit tests for coupon entity, CRUD, and apply logic."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.entities.coupon import Coupon, DiscountType


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
            "discount_type": DiscountType.PERCENTAGE,
            "discount_value": 10,
            "is_active": True,
        }
        defaults.update(kwargs)
        return Coupon(**defaults)

    def test_create_coupon_with_valid_data(self):
        coupon = self._create_coupon()
        assert coupon.code == "SAVE10"
        assert coupon.discount_type == DiscountType.PERCENTAGE
        assert coupon.discount_value == 10
        assert coupon.is_active is True
        assert coupon.current_usage_count == 0

    def test_calculate_discount_percentage(self):
        coupon = self._create_coupon(discount_type=DiscountType.PERCENTAGE, discount_value=10)
        assert coupon.calculate_discount(10000) == 1000  # 10% of 10000

    def test_calculate_discount_fixed_amount(self):
        coupon = self._create_coupon(discount_type=DiscountType.FIXED_AMOUNT, discount_value=500)
        assert coupon.calculate_discount(10000) == 500

    def test_calculate_discount_percentage_with_max_cap(self):
        coupon = self._create_coupon(
            discount_type=DiscountType.PERCENTAGE,
            discount_value=50,
            max_discount_amount=3000,
        )
        # 50% of 10000 = 5000, capped at 3000
        assert coupon.calculate_discount(10000) == 3000

    def test_calculate_discount_fixed_exceeds_subtotal(self):
        coupon = self._create_coupon(discount_type=DiscountType.FIXED_AMOUNT, discount_value=15000)
        # Fixed 15000, but subtotal is only 10000 — capped to subtotal
        assert coupon.calculate_discount(10000) == 10000

    def test_is_valid_active_coupon(self):
        coupon = self._create_coupon()
        is_valid, error = coupon.is_valid(subtotal=5000)
        assert is_valid is True
        assert error is None

    def test_is_valid_inactive_coupon(self):
        coupon = self._create_coupon(is_active=False)
        is_valid, error = coupon.is_valid(subtotal=5000)
        assert is_valid is False
        assert "not active" in error

    def test_is_valid_expired_coupon(self):
        coupon = self._create_coupon(
            valid_to=datetime.now(timezone.utc) - timedelta(days=1),
        )
        is_valid, error = coupon.is_valid(subtotal=5000)
        assert is_valid is False
        assert "expired" in error

    def test_is_valid_not_yet_valid(self):
        coupon = self._create_coupon(
            valid_from=datetime.now(timezone.utc) + timedelta(days=1),
        )
        is_valid, error = coupon.is_valid(subtotal=5000)
        assert is_valid is False
        assert "not yet valid" in error

    def test_is_valid_below_min_order(self):
        coupon = self._create_coupon(min_order_amount=10000)
        is_valid, error = coupon.is_valid(subtotal=5000)
        assert is_valid is False
        assert "Minimum order amount" in error

    def test_is_valid_max_uses_reached(self):
        coupon = self._create_coupon(max_uses=100, current_usage_count=100)
        is_valid, error = coupon.is_valid(subtotal=5000)
        assert is_valid is False
        assert "usage limit" in error

    def test_is_valid_max_per_customer_reached(self):
        coupon = self._create_coupon(max_uses_per_customer=2)
        is_valid, error = coupon.is_valid(subtotal=5000, customer_usage_count=2)
        assert is_valid is False
        assert "maximum number of times" in error

    def test_increment_usage(self):
        coupon = self._create_coupon(current_usage_count=5)
        coupon.increment_usage()
        assert coupon.current_usage_count == 6


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
            "discount_type": DiscountType.PERCENTAGE,
            "discount_value": 20,
            "is_active": True,
        }
        defaults.update(kwargs)
        return Coupon(**defaults)

    def _make_use_case(self, coupon=None, customer_usage=0):
        from src.application.use_cases.coupons.apply_coupon import ApplyCouponUseCase

        mock_repo = AsyncMock()
        mock_repo.get_by_code = AsyncMock(return_value=coupon)
        mock_repo.get_customer_usage_count = AsyncMock(return_value=customer_usage)
        return ApplyCouponUseCase(coupon_repository=mock_repo)

    @pytest.mark.asyncio
    async def test_apply_valid_percentage_coupon(self):
        coupon = self._create_coupon_entity(
            discount_type=DiscountType.PERCENTAGE, discount_value=20,
        )
        use_case = self._make_use_case(coupon=coupon)

        result = await use_case.execute(
            store_id=coupon.store_id, coupon_code="DISCOUNT20", subtotal=10000,
        )
        assert result.calculated_discount == 2000  # 20% of 10000
        assert result.coupon_code == coupon.code

    @pytest.mark.asyncio
    async def test_apply_valid_fixed_coupon(self):
        coupon = self._create_coupon_entity(
            discount_type=DiscountType.FIXED_AMOUNT, discount_value=1500,
        )
        use_case = self._make_use_case(coupon=coupon)

        result = await use_case.execute(
            store_id=coupon.store_id, coupon_code="DISCOUNT20", subtotal=10000,
        )
        assert result.calculated_discount == 1500

    @pytest.mark.asyncio
    async def test_apply_expired_coupon(self):
        coupon = self._create_coupon_entity(
            valid_to=datetime.now(timezone.utc) - timedelta(days=1),
        )
        use_case = self._make_use_case(coupon=coupon)

        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await use_case.execute(
                store_id=coupon.store_id, coupon_code="DISCOUNT20", subtotal=10000,
            )

    @pytest.mark.asyncio
    async def test_apply_coupon_below_min_order(self):
        coupon = self._create_coupon_entity(min_order_amount=20000)
        use_case = self._make_use_case(coupon=coupon)

        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await use_case.execute(
                store_id=coupon.store_id, coupon_code="DISCOUNT20", subtotal=10000,
            )

    @pytest.mark.asyncio
    async def test_apply_coupon_max_uses_exceeded(self):
        coupon = self._create_coupon_entity(max_uses=50, current_usage_count=50)
        use_case = self._make_use_case(coupon=coupon)

        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await use_case.execute(
                store_id=coupon.store_id, coupon_code="DISCOUNT20", subtotal=10000,
            )

    @pytest.mark.asyncio
    async def test_apply_coupon_per_customer_limit(self):
        coupon = self._create_coupon_entity(max_uses_per_customer=1)
        use_case = self._make_use_case(coupon=coupon, customer_usage=1)

        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await use_case.execute(
                store_id=coupon.store_id,
                coupon_code="DISCOUNT20",
                subtotal=10000,
                customer_id=uuid4(),
            )

    @pytest.mark.asyncio
    async def test_apply_nonexistent_coupon(self):
        use_case = self._make_use_case(coupon=None)

        from src.core.exceptions import EntityNotFoundError

        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                store_id=uuid4(), coupon_code="DOESNOTEXIST", subtotal=10000,
            )
