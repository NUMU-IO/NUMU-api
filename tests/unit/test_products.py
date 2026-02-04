"""Unit tests for product CRUD operations and validation."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.value_objects.money import Currency, Money

# ---------------------------------------------------------------------------
# Entity construction
# ---------------------------------------------------------------------------


class TestProductCreation:
    """Tests for creating Product entities."""

    def _make(self, **overrides):
        defaults = {
            "id": uuid4(),
            "store_id": uuid4(),
            "name": "Test Product",
            "slug": "test-product",
            "sku": "SKU-001",
            "product_type": ProductType.PHYSICAL,
            "status": ProductStatus.ACTIVE,
            "price": Money(amount=Decimal("49.99"), currency=Currency.EGP),
            "quantity": 100,
        }
        defaults.update(overrides)
        return Product(**defaults)

    def test_basic_creation(self):
        p = self._make()
        assert p.name == "Test Product"
        assert p.sku == "SKU-001"
        assert p.status == ProductStatus.ACTIVE

    def test_default_quantity_zero_is_out_of_stock(self):
        p = self._make(quantity=0)
        assert not p.is_in_stock

    def test_positive_quantity_is_in_stock(self):
        p = self._make(quantity=10)
        assert p.is_in_stock

    def test_low_stock_detection(self):
        p = self._make(quantity=3, low_stock_threshold=5)
        assert p.is_low_stock

    def test_not_low_stock_when_above_threshold(self):
        p = self._make(quantity=50, low_stock_threshold=5)
        assert not p.is_low_stock

    def test_on_sale_when_compare_at_price_higher(self):
        p = self._make(
            price=Money(amount=Decimal("30.00"), currency=Currency.EGP),
            compare_at_price=Money(amount=Decimal("50.00"), currency=Currency.EGP),
        )
        assert p.is_on_sale

    def test_not_on_sale_without_compare_price(self):
        p = self._make(compare_at_price=None)
        assert not p.is_on_sale


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestProductStatusTransitions:
    """Tests for product status changes."""

    def _make(self, status=ProductStatus.DRAFT):
        return Product(
            id=uuid4(),
            store_id=uuid4(),
            name="Draft Product",
            slug="draft-product",
            price=Money(amount=Decimal("10.00"), currency=Currency.EGP),
            status=status,
        )

    def test_draft_to_active(self):
        p = self._make(status=ProductStatus.DRAFT)
        p.status = ProductStatus.ACTIVE
        assert p.status == ProductStatus.ACTIVE

    def test_active_to_archived(self):
        p = self._make(status=ProductStatus.ACTIVE)
        p.status = ProductStatus.ARCHIVED
        assert p.status == ProductStatus.ARCHIVED

    def test_archived_back_to_active(self):
        p = self._make(status=ProductStatus.ARCHIVED)
        p.status = ProductStatus.ACTIVE
        assert p.status == ProductStatus.ACTIVE


# ---------------------------------------------------------------------------
# Price / money handling
# ---------------------------------------------------------------------------

class TestProductPricing:
    """Tests for price calculations on products."""

    def test_price_in_cents(self):
        price = Money(amount=Decimal("99.99"), currency=Currency.EGP)
        p = Product(
            id=uuid4(),
            store_id=uuid4(),
            name="Priced",
            slug="priced",
            price=price,
        )
        assert p.price.cents == 9999

    def test_zero_price_allowed(self):
        price = Money(amount=Decimal("0.00"), currency=Currency.EGP)
        p = Product(
            id=uuid4(),
            store_id=uuid4(),
            name="Free",
            slug="free",
            price=price,
        )
        assert p.price.cents == 0


# ---------------------------------------------------------------------------
# Quantity management
# ---------------------------------------------------------------------------

class TestProductQuantity:
    """Tests for inventory quantity operations."""

    def _make(self, quantity=100):
        return Product(
            id=uuid4(),
            store_id=uuid4(),
            name="Inventory Test",
            slug="inventory-test",
            price=Money(amount=Decimal("10.00"), currency=Currency.EGP),
            quantity=quantity,
        )

    def test_deduct_quantity(self):
        p = self._make(quantity=50)
        p.quantity -= 5
        assert p.quantity == 45

    def test_deduct_to_zero(self):
        p = self._make(quantity=5)
        p.quantity -= 5
        assert p.quantity == 0
        assert not p.is_in_stock

    def test_restore_quantity(self):
        p = self._make(quantity=0)
        p.quantity += 10
        assert p.quantity == 10
        assert p.is_in_stock


# ---------------------------------------------------------------------------
# Schema validation (Pydantic request schemas)
# ---------------------------------------------------------------------------

class TestProductSchemas:
    """Tests for product Pydantic schemas."""

    def test_create_product_request_valid(self):
        from src.api.v1.schemas.tenant.product import CreateProductRequest

        data = CreateProductRequest(
            store_id=uuid4(),
            name="New Product",
            price=Decimal("25.50"),
        )
        assert data.name == "New Product"
        assert data.price == Decimal("25.50")
        assert data.quantity == 0  # default

    def test_create_product_request_requires_name(self):
        from pydantic import ValidationError

        from src.api.v1.schemas.tenant.product import CreateProductRequest

        with pytest.raises(ValidationError):
            CreateProductRequest(store_id=uuid4(), name="", price=Decimal("10"))

    def test_update_product_request_all_optional(self):
        from src.api.v1.schemas.tenant.product import UpdateProductRequest

        data = UpdateProductRequest()
        assert data.name is None
        assert data.price is None
        assert data.quantity is None

    def test_update_product_request_partial(self):
        from src.api.v1.schemas.tenant.product import UpdateProductRequest

        data = UpdateProductRequest(name="Updated", quantity=42)
        assert data.name == "Updated"
        assert data.quantity == 42
        assert data.price is None
