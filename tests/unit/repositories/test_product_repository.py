"""Unit tests for ProductRepository."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.value_objects.money import Currency, Money
from src.infrastructure.repositories.product_repository import ProductRepository


class TestProductRepository:
    """Tests for ProductRepository."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_session = MagicMock()
        self.mock_session.execute = AsyncMock()
        self.mock_session.add = MagicMock()
        self.mock_session.flush = AsyncMock()
        self.mock_session.refresh = AsyncMock()
        self.mock_session.delete = AsyncMock()
        self.repository = ProductRepository(self.mock_session)

    def _create_sample_product(self, store_id=None) -> Product:
        """Create a sample product for testing."""
        return Product(
            id=uuid4(),
            store_id=store_id or uuid4(),
            name="Test Product",
            slug="test-product",
            sku="TEST-001",
            description="Test description",
            short_description="Test",
            product_type=ProductType.PHYSICAL,
            status=ProductStatus.ACTIVE,
            price=Money(amount=Decimal("99.99"), currency=Currency.USD),
            quantity=100,
            low_stock_threshold=10,
            images=["https://example.com/image.jpg"],
            tags=["test"],
            attributes={"color": "blue"},
        )

    @pytest.mark.asyncio
    async def test_get_by_id_found(self):
        """Test getting product by ID when it exists."""
        product_id = uuid4()
        mock_model = MagicMock()
        mock_model.id = product_id
        mock_model.store_id = uuid4()
        mock_model.name = "Test Product"
        mock_model.slug = "test-product"
        mock_model.sku = "TEST-001"
        mock_model.description = "Test"
        mock_model.short_description = "Test"
        mock_model.product_type = ProductType.PHYSICAL
        mock_model.status = ProductStatus.ACTIVE
        mock_model.price_amount = 9999
        mock_model.price_currency = "USD"
        mock_model.compare_at_price = None
        mock_model.cost_price = None
        mock_model.quantity = 100
        mock_model.low_stock_threshold = 10
        mock_model.weight = None
        mock_model.dimensions = {}
        mock_model.images = []
        mock_model.category_id = None
        mock_model.tags = []
        mock_model.attributes = {}
        mock_model.extra_data = {}
        mock_model.created_at = datetime.utcnow()
        mock_model.updated_at = datetime.utcnow()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_id(product_id)

        assert result is not None
        assert result.id == product_id
        assert result.name == "Test Product"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self):
        """Test getting product by ID when it doesn't exist."""
        product_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_id(product_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_create(self):
        """Test creating a product."""
        product = self._create_sample_product()

        mock_model = MagicMock()
        mock_model.id = product.id
        mock_model.store_id = product.store_id
        mock_model.name = product.name
        mock_model.slug = product.slug
        mock_model.sku = product.sku
        mock_model.description = product.description
        mock_model.short_description = product.short_description
        mock_model.product_type = product.product_type
        mock_model.status = product.status
        mock_model.price_amount = product.price.cents
        mock_model.price_currency = product.price.currency.value
        mock_model.compare_at_price = None
        mock_model.cost_price = None
        mock_model.quantity = product.quantity
        mock_model.low_stock_threshold = product.low_stock_threshold
        mock_model.weight = None
        mock_model.dimensions = {}
        mock_model.images = product.images
        mock_model.category_id = None
        mock_model.tags = product.tags
        mock_model.attributes = product.attributes
        mock_model.extra_data = {}
        mock_model.created_at = product.created_at
        mock_model.updated_at = product.updated_at

        # Mock the refresh to update the model
        self.mock_session.refresh = AsyncMock(return_value=None)

        with patch.object(self.repository, "_to_model", return_value=mock_model):
            with patch.object(self.repository, "_to_entity", return_value=product):
                result = await self.repository.create(product)

        assert result is not None
        self.mock_session.add.assert_called_once()
        self.mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_existing(self):
        """Test deleting an existing product."""
        product_id = uuid4()

        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.delete(product_id)

        assert result is True
        self.mock_session.delete.assert_called_once_with(mock_model)
        self.mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        """Test deleting a non-existent product."""
        product_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.delete(product_id)

        assert result is False
        self.mock_session.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_by_store(self):
        """Test getting products by store ID."""
        store_id = uuid4()

        mock_models = [MagicMock() for _ in range(3)]
        for i, model in enumerate(mock_models):
            model.id = uuid4()
            model.store_id = store_id
            model.name = f"Product {i}"
            model.slug = f"product-{i}"
            model.sku = f"SKU-{i}"
            model.description = "Test"
            model.short_description = "Test"
            model.product_type = ProductType.PHYSICAL
            model.status = ProductStatus.ACTIVE
            model.price_amount = 1000 * (i + 1)
            model.price_currency = "USD"
            model.compare_at_price = None
            model.cost_price = None
            model.quantity = 100
            model.low_stock_threshold = 10
            model.weight = None
            model.dimensions = {}
            model.images = []
            model.category_id = None
            model.tags = []
            model.attributes = {}
            model.extra_data = {}
            model.created_at = datetime.utcnow()
            model.updated_at = datetime.utcnow()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_models
        self.mock_session.execute.return_value = mock_result

        results = await self.repository.get_by_store(store_id)

        assert len(results) == 3
        self.mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_slug(self):
        """Test getting product by slug."""
        store_id = uuid4()
        slug = "test-product"

        mock_model = MagicMock()
        mock_model.id = uuid4()
        mock_model.store_id = store_id
        mock_model.name = "Test Product"
        mock_model.slug = slug
        mock_model.sku = "TEST-001"
        mock_model.description = "Test"
        mock_model.short_description = "Test"
        mock_model.product_type = ProductType.PHYSICAL
        mock_model.status = ProductStatus.ACTIVE
        mock_model.price_amount = 9999
        mock_model.price_currency = "USD"
        mock_model.compare_at_price = None
        mock_model.cost_price = None
        mock_model.quantity = 100
        mock_model.low_stock_threshold = 10
        mock_model.weight = None
        mock_model.dimensions = {}
        mock_model.images = []
        mock_model.category_id = None
        mock_model.tags = []
        mock_model.attributes = {}
        mock_model.extra_data = {}
        mock_model.created_at = datetime.utcnow()
        mock_model.updated_at = datetime.utcnow()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_slug(store_id, slug)

        assert result is not None
        assert result.slug == slug

    @pytest.mark.asyncio
    async def test_count_by_store(self):
        """Test counting products by store."""
        store_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar.return_value = 42
        self.mock_session.execute.return_value = mock_result

        count = await self.repository.count_by_store(store_id)

        assert count == 42

    @pytest.mark.asyncio
    async def test_search(self):
        """Test searching products."""
        store_id = uuid4()
        query = "test"

        mock_models = [MagicMock() for _ in range(2)]
        for i, model in enumerate(mock_models):
            model.id = uuid4()
            model.store_id = store_id
            model.name = f"Test Product {i}"
            model.slug = f"test-product-{i}"
            model.sku = f"TEST-{i}"
            model.description = "Test description"
            model.short_description = "Test"
            model.product_type = ProductType.PHYSICAL
            model.status = ProductStatus.ACTIVE
            model.price_amount = 1000
            model.price_currency = "USD"
            model.compare_at_price = None
            model.cost_price = None
            model.quantity = 100
            model.low_stock_threshold = 10
            model.weight = None
            model.dimensions = {}
            model.images = []
            model.category_id = None
            model.tags = []
            model.attributes = {}
            model.extra_data = {}
            model.created_at = datetime.utcnow()
            model.updated_at = datetime.utcnow()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_models
        self.mock_session.execute.return_value = mock_result

        results = await self.repository.search(store_id, query)

        assert len(results) == 2
