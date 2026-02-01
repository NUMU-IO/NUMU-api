"""Unit tests for product schemas."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.v1.schemas.tenant.product import (
    CreateProductRequest,
    UpdateProductRequest,
    ProductResponse,
)


class TestCreateProductRequest:
    """Tests for CreateProductRequest schema."""

    def test_valid_minimal_product(self):
        """Test creating product with minimal required fields."""
        data = {
            "store_id": str(uuid4()),
            "name": "Test Product",
            "price": "19.99",
        }
        request = CreateProductRequest(**data)

        assert request.name == "Test Product"
        assert request.price == Decimal("19.99")
        assert request.price_currency == "USD"  # Default
        assert request.quantity == 0  # Default

    def test_valid_full_product(self):
        """Test creating product with all fields."""
        category_id = uuid4()
        data = {
            "store_id": str(uuid4()),
            "name": "Full Product",
            "slug": "full-product",
            "sku": "FULL-001",
            "description": "A full product description",
            "short_description": "Short desc",
            "product_type": "physical",
            "price": "99.99",
            "price_currency": "EGP",
            "compare_at_price": "129.99",
            "cost_price": "50.00",
            "quantity": 100,
            "low_stock_threshold": 10,
            "images": ["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
            "category_id": str(category_id),
            "tags": ["electronics", "sale"],
            "attributes": {"color": "blue", "size": "large"},
        }
        request = CreateProductRequest(**data)

        assert request.name == "Full Product"
        assert request.sku == "FULL-001"
        assert request.price == Decimal("99.99")
        assert request.compare_at_price == Decimal("129.99")
        assert len(request.images) == 2
        assert request.category_id == category_id

    def test_invalid_empty_name(self):
        """Test validation fails for empty name."""
        data = {
            "store_id": str(uuid4()),
            "name": "",
            "price": "19.99",
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateProductRequest(**data)

        assert "name" in str(exc_info.value).lower()

    def test_invalid_negative_price(self):
        """Test validation fails for negative price."""
        data = {
            "store_id": str(uuid4()),
            "name": "Test Product",
            "price": "-10.00",
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateProductRequest(**data)

        assert "price" in str(exc_info.value).lower()

    def test_invalid_negative_quantity(self):
        """Test validation fails for negative quantity."""
        data = {
            "store_id": str(uuid4()),
            "name": "Test Product",
            "price": "19.99",
            "quantity": -5,
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateProductRequest(**data)

        assert "quantity" in str(exc_info.value).lower()

    def test_name_too_long(self):
        """Test validation fails for name exceeding max length."""
        data = {
            "store_id": str(uuid4()),
            "name": "A" * 300,  # Exceeds 255 char limit
            "price": "19.99",
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateProductRequest(**data)

        assert "name" in str(exc_info.value).lower()


class TestUpdateProductRequest:
    """Tests for UpdateProductRequest schema."""

    def test_valid_partial_update(self):
        """Test partial update with only some fields."""
        data = {
            "name": "Updated Name",
            "price": "29.99",
        }
        request = UpdateProductRequest(**data)

        assert request.name == "Updated Name"
        assert request.price == Decimal("29.99")
        assert request.description is None  # Not provided

    def test_valid_empty_update(self):
        """Test update with no fields (all optional)."""
        request = UpdateProductRequest()

        assert request.name is None
        assert request.price is None
        assert request.quantity is None

    def test_valid_status_update(self):
        """Test updating product status."""
        data = {
            "status": "active",
        }
        request = UpdateProductRequest(**data)

        assert request.status == "active"

    def test_invalid_negative_price_update(self):
        """Test validation fails for negative price in update."""
        data = {
            "price": "-5.00",
        }
        with pytest.raises(ValidationError):
            UpdateProductRequest(**data)


class TestProductResponse:
    """Tests for ProductResponse schema."""

    def test_valid_response(self):
        """Test valid product response."""
        data = {
            "id": str(uuid4()),
            "store_id": str(uuid4()),
            "name": "Test Product",
            "slug": "test-product",
            "sku": "TEST-001",
            "description": "A test product",
            "short_description": "Test",
            "product_type": "physical",
            "status": "active",
            "price": "19.99",
            "price_currency": "USD",
            "compare_at_price": None,
            "cost_price": None,
            "quantity": 100,
            "is_in_stock": True,
            "is_low_stock": False,
            "is_on_sale": False,
            "images": [],
            "category_id": None,
            "tags": [],
            "attributes": {},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        response = ProductResponse(**data)

        assert response.name == "Test Product"
        assert response.is_in_stock is True
        assert response.is_on_sale is False

    def test_response_with_sale_price(self):
        """Test product response with sale price."""
        data = {
            "id": str(uuid4()),
            "store_id": str(uuid4()),
            "name": "Sale Product",
            "slug": "sale-product",
            "sku": None,
            "description": None,
            "short_description": None,
            "product_type": "physical",
            "status": "active",
            "price": "49.99",
            "price_currency": "USD",
            "compare_at_price": "79.99",
            "cost_price": None,
            "quantity": 50,
            "is_in_stock": True,
            "is_low_stock": False,
            "is_on_sale": True,
            "images": ["https://example.com/sale.jpg"],
            "category_id": None,
            "tags": ["sale"],
            "attributes": {},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        response = ProductResponse(**data)

        assert response.is_on_sale is True
        assert response.compare_at_price == "79.99"
