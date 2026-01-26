"""Unit tests for Product entity."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.value_objects.money import Money, Currency


class TestProductEntity:
    """Tests for the Product entity."""

    def _create_product(self, **kwargs) -> Product:
        """Helper to create a product."""
        defaults = {
            "id": uuid4(),
            "store_id": uuid4(),
            "name": "Test Product",
            "slug": "test-product",
            "price": Money(amount=Decimal("19.99"), currency=Currency.USD),
        }
        defaults.update(kwargs)
        return Product(**defaults)

    def test_create_product_with_valid_data(self):
        """Test creating a product with valid data."""
        product = self._create_product()

        assert product.name == "Test Product"
        assert product.slug == "test-product"
        assert product.price.amount == Decimal("19.99")
        assert product.status == ProductStatus.DRAFT  # Default

    def test_product_is_in_stock(self):
        """Test is_in_stock property."""
        product = self._create_product(quantity=10)
        assert product.is_in_stock is True

        out_of_stock = self._create_product(quantity=0)
        assert out_of_stock.is_in_stock is False

    def test_product_is_low_stock(self):
        """Test is_low_stock property."""
        # Low stock threshold is 5 by default
        low_stock = self._create_product(quantity=3)
        assert low_stock.is_low_stock is True

        normal_stock = self._create_product(quantity=10)
        assert normal_stock.is_low_stock is False

        out_of_stock = self._create_product(quantity=0)
        assert out_of_stock.is_low_stock is False  # 0 is out, not low

    def test_product_is_out_of_stock(self):
        """Test is_out_of_stock property."""
        out_of_stock = self._create_product(quantity=0)
        assert out_of_stock.is_out_of_stock is True

        in_stock = self._create_product(quantity=5)
        assert in_stock.is_out_of_stock is False

    def test_product_is_on_sale(self):
        """Test is_on_sale property."""
        # Not on sale - no compare_at_price
        regular = self._create_product()
        assert regular.is_on_sale is False

        # On sale - compare_at_price > price
        on_sale = self._create_product(
            price=Money(amount=Decimal("15.00"), currency=Currency.USD),
            compare_at_price=Money(amount=Decimal("20.00"), currency=Currency.USD),
        )
        assert on_sale.is_on_sale is True

    def test_product_discount_percentage(self):
        """Test discount_percentage property."""
        # 25% off ($20 -> $15)
        product = self._create_product(
            price=Money(amount=Decimal("15.00"), currency=Currency.USD),
            compare_at_price=Money(amount=Decimal("20.00"), currency=Currency.USD),
        )
        assert product.discount_percentage == 25.0

        # No discount
        regular = self._create_product()
        assert regular.discount_percentage == 0.0

    def test_product_profit_margin(self):
        """Test profit_margin property."""
        product = self._create_product(
            price=Money(amount=Decimal("100.00"), currency=Currency.USD),
            cost_price=Money(amount=Decimal("60.00"), currency=Currency.USD),
        )
        # (100 - 60) / 100 * 100 = 40%
        assert product.profit_margin == 40.0

        # No cost price
        no_cost = self._create_product()
        assert no_cost.profit_margin is None

    def test_product_is_published(self):
        """Test is_published property."""
        active = self._create_product(status=ProductStatus.ACTIVE)
        assert active.is_published is True

        draft = self._create_product(status=ProductStatus.DRAFT)
        assert draft.is_published is False

    def test_product_is_draft(self):
        """Test is_draft property."""
        draft = self._create_product(status=ProductStatus.DRAFT)
        assert draft.is_draft is True

        active = self._create_product(status=ProductStatus.ACTIVE)
        assert active.is_draft is False

    def test_product_is_archived(self):
        """Test is_archived property."""
        archived = self._create_product(status=ProductStatus.ARCHIVED)
        assert archived.is_archived is True

    def test_product_update_quantity_positive(self):
        """Test update_quantity with positive delta."""
        product = self._create_product(quantity=10)
        product.update_quantity(5)
        assert product.quantity == 15

    def test_product_update_quantity_negative(self):
        """Test update_quantity with negative delta."""
        product = self._create_product(quantity=10)
        product.update_quantity(-3)
        assert product.quantity == 7

    def test_product_update_quantity_to_zero_sets_out_of_stock(self):
        """Test update_quantity to zero sets OUT_OF_STOCK status."""
        product = self._create_product(quantity=5, status=ProductStatus.ACTIVE)
        product.update_quantity(-5)
        assert product.quantity == 0
        assert product.status == ProductStatus.OUT_OF_STOCK

    def test_product_update_quantity_from_zero_restores_active(self):
        """Test update_quantity from zero restores ACTIVE status."""
        product = self._create_product(quantity=0, status=ProductStatus.OUT_OF_STOCK)
        product.update_quantity(10)
        assert product.quantity == 10
        assert product.status == ProductStatus.ACTIVE

    def test_product_update_quantity_negative_result_raises(self):
        """Test update_quantity with result < 0 raises error."""
        product = self._create_product(quantity=5)

        with pytest.raises(ValueError, match="Cannot reduce quantity"):
            product.update_quantity(-10)

    def test_product_set_quantity(self):
        """Test set_quantity method."""
        product = self._create_product(quantity=10)
        product.set_quantity(25)
        assert product.quantity == 25

    def test_product_set_quantity_negative_raises(self):
        """Test set_quantity with negative value raises error."""
        product = self._create_product(quantity=10)

        with pytest.raises(ValueError, match="negative"):
            product.set_quantity(-5)

    def test_product_publish(self):
        """Test publish method."""
        product = self._create_product(status=ProductStatus.DRAFT)
        product.publish()
        assert product.status == ProductStatus.ACTIVE

    def test_product_unpublish(self):
        """Test unpublish method."""
        product = self._create_product(status=ProductStatus.ACTIVE)
        product.unpublish()
        assert product.status == ProductStatus.DRAFT

    def test_product_archive(self):
        """Test archive method."""
        product = self._create_product(status=ProductStatus.ACTIVE)
        product.archive()
        assert product.status == ProductStatus.ARCHIVED

    def test_product_restore(self):
        """Test restore method."""
        product = self._create_product(status=ProductStatus.ARCHIVED)
        product.restore()
        assert product.status == ProductStatus.DRAFT

    def test_product_restore_non_archived_does_nothing(self):
        """Test restore on non-archived product does nothing."""
        product = self._create_product(status=ProductStatus.ACTIVE)
        product.restore()
        assert product.status == ProductStatus.ACTIVE  # Unchanged

    def test_product_add_image(self):
        """Test add_image method."""
        product = self._create_product()
        product.add_image("https://example.com/image1.jpg")
        product.add_image("https://example.com/image2.jpg")

        assert len(product.images) == 2
        assert "https://example.com/image1.jpg" in product.images

    def test_product_add_duplicate_image_ignored(self):
        """Test add_image ignores duplicates."""
        product = self._create_product()
        product.add_image("https://example.com/image1.jpg")
        product.add_image("https://example.com/image1.jpg")

        assert len(product.images) == 1

    def test_product_remove_image(self):
        """Test remove_image method."""
        product = self._create_product(images=["https://example.com/image1.jpg"])
        product.remove_image("https://example.com/image1.jpg")

        assert len(product.images) == 0

    def test_product_add_tag(self):
        """Test add_tag method."""
        product = self._create_product()
        product.add_tag("Electronics")
        product.add_tag("Featured")

        assert "electronics" in product.tags  # Normalized
        assert "featured" in product.tags

    def test_product_add_tag_normalized(self):
        """Test add_tag normalizes to lowercase."""
        product = self._create_product()
        product.add_tag("  SALE  ")

        assert "sale" in product.tags

    def test_product_remove_tag(self):
        """Test remove_tag method."""
        product = self._create_product(tags=["electronics", "sale"])
        product.remove_tag("ELECTRONICS")  # Case insensitive

        assert "electronics" not in product.tags
        assert "sale" in product.tags

    def test_product_set_attribute(self):
        """Test set_attribute method."""
        product = self._create_product()
        product.set_attribute("color", "red")
        product.set_attribute("size", "M")

        assert product.attributes["color"] == "red"
        assert product.attributes["size"] == "M"

    def test_product_remove_attribute(self):
        """Test remove_attribute method."""
        product = self._create_product(attributes={"color": "red", "size": "M"})
        product.remove_attribute("color")

        assert "color" not in product.attributes
        assert "size" in product.attributes

    def test_product_types(self):
        """Test different product types."""
        physical = self._create_product(product_type=ProductType.PHYSICAL)
        assert physical.product_type == ProductType.PHYSICAL

        digital = self._create_product(product_type=ProductType.DIGITAL)
        assert digital.product_type == ProductType.DIGITAL

        service = self._create_product(product_type=ProductType.SERVICE)
        assert service.product_type == ProductType.SERVICE

    def test_product_serialization(self):
        """Test product serialization to dict."""
        product = self._create_product(
            name="Serialization Test",
            quantity=50,
            tags=["test"],
        )

        data = product.model_dump()
        assert data["name"] == "Serialization Test"
        assert data["quantity"] == 50
        assert "test" in data["tags"]
