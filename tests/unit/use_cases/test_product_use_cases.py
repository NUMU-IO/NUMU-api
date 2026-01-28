"""Unit tests for product use cases."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.dto.product import UpdateProductDTO
from src.application.use_cases.products.get_product import GetProductUseCase
from src.application.use_cases.products.list_products import ListProductsUseCase
from src.application.use_cases.products.update_product import UpdateProductUseCase
from src.application.use_cases.products.delete_product import DeleteProductUseCase
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.value_objects.money import Currency, Money


class TestGetProductUseCase:
    """Tests for GetProductUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_product_repo = MagicMock()
        self.mock_product_repo.get_by_id = AsyncMock()
        self.mock_product_repo.get_by_slug = AsyncMock()

        self.use_case = GetProductUseCase(product_repository=self.mock_product_repo)

        self.product_id = uuid4()
        self.store_id = uuid4()
        self.sample_product = Product(
            id=self.product_id,
            store_id=self.store_id,
            name="Test Product",
            slug="test-product",
            sku="TEST-001",
            product_type=ProductType.PHYSICAL,
            status=ProductStatus.ACTIVE,
            price=Money(amount=Decimal("49.99"), currency=Currency.USD),
            quantity=100,
        )

    @pytest.mark.asyncio
    async def test_get_product_success(self):
        """Test successful product retrieval."""
        self.mock_product_repo.get_by_id.return_value = self.sample_product

        result = await self.use_case.execute(product_id=self.product_id)

        assert result is not None
        assert result.id == self.product_id
        assert result.name == "Test Product"

    @pytest.mark.asyncio
    async def test_get_product_not_found(self):
        """Test product retrieval when not found."""
        self.mock_product_repo.get_by_id.return_value = None

        with pytest.raises(EntityNotFoundError):
            await self.use_case.execute(product_id=uuid4())

    @pytest.mark.asyncio
    async def test_get_product_by_slug_success(self):
        """Test successful product retrieval by slug."""
        self.mock_product_repo.get_by_slug.return_value = self.sample_product

        result = await self.use_case.by_slug(store_id=self.store_id, slug="test-product")

        assert result is not None
        assert result.slug == "test-product"

    @pytest.mark.asyncio
    async def test_get_product_by_slug_not_found(self):
        """Test product retrieval by slug when not found."""
        self.mock_product_repo.get_by_slug.return_value = None

        with pytest.raises(EntityNotFoundError):
            await self.use_case.by_slug(store_id=self.store_id, slug="nonexistent")


class TestListProductsUseCase:
    """Tests for ListProductsUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_product_repo = MagicMock()
        self.mock_product_repo.list_with_filters = AsyncMock()
        self.mock_product_repo.count_with_filters = AsyncMock()
        self.mock_product_repo.search = AsyncMock()
        self.mock_product_repo.get_by_category = AsyncMock()

        self.use_case = ListProductsUseCase(product_repository=self.mock_product_repo)

        self.store_id = uuid4()

    @pytest.mark.asyncio
    async def test_list_products_success(self):
        """Test successful product listing."""
        products = [
            Product(
                id=uuid4(),
                store_id=self.store_id,
                name=f"Product {i}",
                slug=f"product-{i}",
                price=Money(amount=Decimal("10.00"), currency=Currency.USD),
            )
            for i in range(3)
        ]
        self.mock_product_repo.list_with_filters.return_value = products
        self.mock_product_repo.count_with_filters.return_value = 3

        result = await self.use_case.execute(store_id=self.store_id)

        assert len(result.items) == 3
        assert result.total == 3

    @pytest.mark.asyncio
    async def test_list_products_empty(self):
        """Test listing products when store has none."""
        self.mock_product_repo.list_with_filters.return_value = []
        self.mock_product_repo.count_with_filters.return_value = 0

        result = await self.use_case.execute(store_id=self.store_id)

        assert len(result.items) == 0
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_list_products_with_pagination(self):
        """Test product listing with pagination."""
        products = [
            Product(
                id=uuid4(),
                store_id=self.store_id,
                name=f"Product {i}",
                slug=f"product-{i}",
                price=Money(amount=Decimal("10.00"), currency=Currency.USD),
            )
            for i in range(10)
        ]
        self.mock_product_repo.list_with_filters.return_value = products[:5]
        self.mock_product_repo.count_with_filters.return_value = 10

        result = await self.use_case.execute(store_id=self.store_id, skip=0, limit=5)

        assert len(result.items) == 5
        assert result.total == 10
        assert result.page == 1
        assert result.page_size == 5

    @pytest.mark.asyncio
    async def test_list_products_search(self):
        """Test product search."""
        products = [
            Product(
                id=uuid4(),
                store_id=self.store_id,
                name="Blue Widget",
                slug="blue-widget",
                price=Money(amount=Decimal("10.00"), currency=Currency.USD),
            )
        ]
        self.mock_product_repo.search.return_value = products

        result = await self.use_case.search(
            store_id=self.store_id, query="blue", page=1, page_size=20
        )

        assert len(result.items) == 1
        assert "Blue" in result.items[0].name

    @pytest.mark.asyncio
    async def test_list_products_by_category(self):
        """Test listing products by category."""
        category_id = uuid4()
        products = [
            Product(
                id=uuid4(),
                store_id=self.store_id,
                name="Category Product",
                slug="category-product",
                category_id=category_id,
                price=Money(amount=Decimal("10.00"), currency=Currency.USD),
            )
        ]
        self.mock_product_repo.get_by_category.return_value = products

        result = await self.use_case.by_category(category_id=category_id)

        assert len(result.items) == 1


class TestUpdateProductUseCase:
    """Tests for UpdateProductUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_product_repo = MagicMock()
        self.mock_product_repo.get_by_id = AsyncMock()
        self.mock_product_repo.update = AsyncMock()

        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()

        self.use_case = UpdateProductUseCase(
            product_repository=self.mock_product_repo,
            store_repository=self.mock_store_repo,
        )

        self.user_id = uuid4()
        self.store_id = uuid4()
        self.product_id = uuid4()

        self.sample_store = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

        self.sample_product = Product(
            id=self.product_id,
            store_id=self.store_id,
            name="Original Product",
            slug="original-product",
            price=Money(amount=Decimal("49.99"), currency=Currency.USD),
            quantity=100,
        )

    @pytest.mark.asyncio
    async def test_update_product_success(self):
        """Test successful product update."""
        self.mock_product_repo.get_by_id.return_value = self.sample_product
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        updated_product = Product(
            id=self.product_id,
            store_id=self.store_id,
            name="Updated Product",
            slug="original-product",
            price=Money(amount=Decimal("59.99"), currency=Currency.USD),
            quantity=100,
        )
        self.mock_product_repo.update.return_value = updated_product

        dto = UpdateProductDTO(name="Updated Product", price=Decimal("59.99"))

        result = await self.use_case.execute(
            product_id=self.product_id,
            dto=dto,
            user_id=self.user_id,
        )

        assert result.name == "Updated Product"
        self.mock_product_repo.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_product_not_found(self):
        """Test update when product not found."""
        self.mock_product_repo.get_by_id.return_value = None

        dto = UpdateProductDTO(name="Updated Product")

        with pytest.raises(EntityNotFoundError):
            await self.use_case.execute(
                product_id=self.product_id,
                dto=dto,
                user_id=self.user_id,
            )

    @pytest.mark.asyncio
    async def test_update_product_not_owner(self):
        """Test update by non-owner."""
        other_user_id = uuid4()
        self.mock_product_repo.get_by_id.return_value = self.sample_product
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        dto = UpdateProductDTO(name="Hacked Product")

        with pytest.raises(AuthorizationError):
            await self.use_case.execute(
                product_id=self.product_id,
                dto=dto,
                user_id=other_user_id,  # Different user
            )

    @pytest.mark.asyncio
    async def test_update_product_partial_update(self):
        """Test partial product update."""
        self.mock_product_repo.get_by_id.return_value = self.sample_product
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        updated_product = Product(
            id=self.product_id,
            store_id=self.store_id,
            name="Original Product",
            slug="original-product",
            price=Money(amount=Decimal("49.99"), currency=Currency.USD),
            quantity=50,  # Updated
        )
        self.mock_product_repo.update.return_value = updated_product

        dto = UpdateProductDTO(quantity=50)

        result = await self.use_case.execute(
            product_id=self.product_id,
            dto=dto,
            user_id=self.user_id,
        )

        assert result.quantity == 50


class TestDeleteProductUseCase:
    """Tests for DeleteProductUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_product_repo = MagicMock()
        self.mock_product_repo.get_by_id = AsyncMock()
        self.mock_product_repo.delete = AsyncMock(return_value=True)

        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()

        self.use_case = DeleteProductUseCase(
            product_repository=self.mock_product_repo,
            store_repository=self.mock_store_repo,
        )

        self.user_id = uuid4()
        self.store_id = uuid4()
        self.product_id = uuid4()

        self.sample_store = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

        self.sample_product = Product(
            id=self.product_id,
            store_id=self.store_id,
            name="Product to Delete",
            slug="product-to-delete",
            price=Money(amount=Decimal("49.99"), currency=Currency.USD),
        )

    @pytest.mark.asyncio
    async def test_delete_product_success(self):
        """Test successful product deletion."""
        self.mock_product_repo.get_by_id.return_value = self.sample_product
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        await self.use_case.execute(product_id=self.product_id, user_id=self.user_id)

        self.mock_product_repo.delete.assert_called_once_with(self.product_id)

    @pytest.mark.asyncio
    async def test_delete_product_not_found(self):
        """Test deletion when product not found."""
        self.mock_product_repo.get_by_id.return_value = None

        with pytest.raises(EntityNotFoundError):
            await self.use_case.execute(
                product_id=self.product_id,
                user_id=self.user_id,
            )

    @pytest.mark.asyncio
    async def test_delete_product_not_owner(self):
        """Test deletion by non-owner."""
        other_user_id = uuid4()
        self.mock_product_repo.get_by_id.return_value = self.sample_product
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        with pytest.raises(AuthorizationError):
            await self.use_case.execute(
                product_id=self.product_id,
                user_id=other_user_id,  # Different user
            )
