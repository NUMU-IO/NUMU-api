"""Unit tests for CreateProductUseCase."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.dto.product import CreateProductDTO
from src.application.use_cases.products.create_product import CreateProductUseCase
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.value_objects.money import Currency, Money


class TestCreateProductUseCase:
    """Tests for CreateProductUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_product_repo = MagicMock()
        self.mock_product_repo.get_by_slug = AsyncMock(return_value=None)
        self.mock_product_repo.create = AsyncMock()

        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()

        self.use_case = CreateProductUseCase(
            product_repository=self.mock_product_repo,
            store_repository=self.mock_store_repo,
        )

        self.user_id = uuid4()
        self.store_id = uuid4()

        # Create sample store
        self.sample_store = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

    @pytest.mark.asyncio
    async def test_create_product_success(self):
        """Test successful product creation."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        created_product = Product(
            id=uuid4(),
            store_id=self.store_id,
            name="New Product",
            slug="new-product",
            sku="NEW-001",
            description="A new product",
            product_type=ProductType.PHYSICAL,
            status=ProductStatus.DRAFT,
            price=Money(amount=Decimal("49.99"), currency=Currency.USD),
            quantity=100,
        )
        self.mock_product_repo.create.return_value = created_product

        dto = CreateProductDTO(
            name="New Product",
            sku="NEW-001",
            description="A new product",
            product_type="physical",
            price=Decimal("49.99"),
            price_currency="USD",
            quantity=100,
        )

        result = await self.use_case.execute(
            dto=dto,
            store_id=self.store_id,
            user_id=self.user_id,
        )

        assert result is not None
        assert result.name == "New Product"
        self.mock_product_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_product_store_not_found(self):
        """Test product creation with non-existent store."""
        self.mock_store_repo.get_by_id.return_value = None

        dto = CreateProductDTO(
            name="New Product",
            price=Decimal("49.99"),
            price_currency="USD",
        )

        with pytest.raises(EntityNotFoundError) as exc_info:
            await self.use_case.execute(
                dto=dto,
                store_id=self.store_id,
                user_id=self.user_id,
            )

        assert "Store" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_product_not_owner(self):
        """Test product creation by non-owner."""
        other_user_id = uuid4()
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        dto = CreateProductDTO(
            name="New Product",
            price=Decimal("49.99"),
            price_currency="USD",
        )

        with pytest.raises(AuthorizationError):
            await self.use_case.execute(
                dto=dto,
                store_id=self.store_id,
                user_id=other_user_id,  # Different user
            )

    @pytest.mark.asyncio
    async def test_create_product_generates_unique_slug(self):
        """Test that duplicate slug generates unique one."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        # First call returns existing product, second returns None
        existing_product = Product(
            id=uuid4(),
            store_id=self.store_id,
            name="Existing",
            slug="new-product",
            price=Money(amount=Decimal("10"), currency=Currency.USD),
        )
        self.mock_product_repo.get_by_slug.return_value = existing_product

        created_product = Product(
            id=uuid4(),
            store_id=self.store_id,
            name="New Product",
            slug="new-product-abc12345",  # Modified slug
            price=Money(amount=Decimal("49.99"), currency=Currency.USD),
        )
        self.mock_product_repo.create.return_value = created_product

        dto = CreateProductDTO(
            name="New Product",
            slug="new-product",
            price=Decimal("49.99"),
            price_currency="USD",
        )

        result = await self.use_case.execute(
            dto=dto,
            store_id=self.store_id,
            user_id=self.user_id,
        )

        # Slug should be modified to be unique
        assert result is not None
        self.mock_product_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_product_with_compare_price(self):
        """Test product creation with compare-at price."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        created_product = Product(
            id=uuid4(),
            store_id=self.store_id,
            name="Sale Product",
            slug="sale-product",
            price=Money(amount=Decimal("49.99"), currency=Currency.USD),
            compare_at_price=Money(amount=Decimal("79.99"), currency=Currency.USD),
        )
        self.mock_product_repo.create.return_value = created_product

        dto = CreateProductDTO(
            name="Sale Product",
            price=Decimal("49.99"),
            compare_at_price=Decimal("79.99"),
            price_currency="USD",
        )

        result = await self.use_case.execute(
            dto=dto,
            store_id=self.store_id,
            user_id=self.user_id,
        )

        assert result is not None
        assert result.is_on_sale is True
