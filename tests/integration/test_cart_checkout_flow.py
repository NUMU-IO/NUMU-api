"""Integration tests for cart and checkout flow."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.dto.cart import AddToCartDTO, UpdateCartItemDTO
from src.application.dto.checkout import CheckoutAddressDTO, CheckoutDTO
from src.application.use_cases.storefront.cart import (
    AddToCartUseCase,
    ClearCartUseCase,
    GetCartUseCase,
    RemoveCartItemUseCase,
    UpdateCartItemUseCase,
)
from src.application.use_cases.storefront.checkout import CheckoutUseCase
from src.core.entities.cart import Cart, CartItem
from src.core.entities.customer import Customer
from src.core.entities.order import Order, OrderStatus, PaymentStatus
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.exceptions import EntityNotFoundError, InsufficientStockError, ValidationError
from src.core.value_objects.money import Currency, Money


# ============================================================================
# Test Fixtures
# ============================================================================


def make_store(store_id=None, owner_id=None) -> Store:
    """Create a test store."""
    return Store(
        id=store_id or uuid4(),
        owner_id=owner_id or uuid4(),
        name="Test Store",
        slug="test-store",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
        tenant_id=uuid4(),
    )


def make_product(store_id, product_id=None, price_cents=10000, quantity=50) -> Product:
    """Create a test product."""
    return Product(
        id=product_id or uuid4(),
        store_id=store_id,
        name="Test Product",
        slug="test-product",
        sku="TEST-001",
        product_type=ProductType.PHYSICAL,
        status=ProductStatus.ACTIVE,
        price=Money(amount=Decimal(str(price_cents)), currency=Currency.EGP),
        quantity=quantity,
    )


def make_cart(store_id, customer_id, cart_id=None) -> Cart:
    """Create a test cart."""
    return Cart(
        id=cart_id or uuid4(),
        store_id=store_id,
        customer_id=customer_id,
    )


def make_checkout_address() -> CheckoutAddressDTO:
    """Create a test checkout address."""
    return CheckoutAddressDTO(
        first_name="Test",
        last_name="Customer",
        address_line1="123 Test St",
        city="Cairo",
        country="Egypt",
    )


# ============================================================================
# Cart Use Case Tests
# ============================================================================


class TestAddToCartUseCase:
    """Tests for AddToCartUseCase."""

    def setup_method(self):
        self.store_id = uuid4()
        self.customer_id = uuid4()
        self.tenant_id = uuid4()
        self.product = make_product(self.store_id, quantity=10)
        self.cart = make_cart(self.store_id, self.customer_id)

        self.cart_repo = AsyncMock()
        self.product_repo = AsyncMock()

        self.cart_repo.get_or_create_cart.return_value = self.cart
        self.cart_repo.get_active_cart.return_value = self.cart
        self.cart_repo.update.return_value = self.cart
        self.product_repo.get_by_id.return_value = self.product
        self.product_repo.get_by_ids.return_value = {self.product.id: self.product}

    @pytest.mark.asyncio
    async def test_add_item_to_empty_cart(self):
        """Test adding an item to an empty cart."""
        use_case = AddToCartUseCase(
            cart_repository=self.cart_repo,
            product_repository=self.product_repo,
        )

        dto = AddToCartDTO(product_id=self.product.id, quantity=2)
        result = await use_case.execute(
            store_id=self.store_id,
            customer_id=self.customer_id,
            tenant_id=self.tenant_id,
            dto=dto,
        )

        assert result is not None
        self.cart_repo.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_item_product_not_found_raises(self):
        """Test adding a non-existent product raises error."""
        self.product_repo.get_by_id.return_value = None

        use_case = AddToCartUseCase(
            cart_repository=self.cart_repo,
            product_repository=self.product_repo,
        )

        dto = AddToCartDTO(product_id=uuid4(), quantity=1)
        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                tenant_id=self.tenant_id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_add_item_insufficient_stock_raises(self):
        """Test adding more than available stock raises error."""
        self.product.quantity = 2

        use_case = AddToCartUseCase(
            cart_repository=self.cart_repo,
            product_repository=self.product_repo,
        )

        dto = AddToCartDTO(product_id=self.product.id, quantity=5)
        with pytest.raises(InsufficientStockError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                tenant_id=self.tenant_id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_add_item_wrong_store_raises(self):
        """Test adding product from different store raises error."""
        self.product.store_id = uuid4()  # Different store

        use_case = AddToCartUseCase(
            cart_repository=self.cart_repo,
            product_repository=self.product_repo,
        )

        dto = AddToCartDTO(product_id=self.product.id, quantity=1)
        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                tenant_id=self.tenant_id,
                dto=dto,
            )


class TestUpdateCartItemUseCase:
    """Tests for UpdateCartItemUseCase."""

    def setup_method(self):
        self.store_id = uuid4()
        self.customer_id = uuid4()
        self.tenant_id = uuid4()
        self.product = make_product(self.store_id, quantity=10)
        self.cart = make_cart(self.store_id, self.customer_id)
        self.cart_item = self.cart.add_item(product_id=self.product.id, quantity=2)

        self.cart_repo = AsyncMock()
        self.product_repo = AsyncMock()

        self.cart_repo.get_active_cart.return_value = self.cart
        self.cart_repo.get_or_create_cart.return_value = self.cart
        self.cart_repo.update.return_value = self.cart
        self.product_repo.get_by_id.return_value = self.product
        self.product_repo.get_by_ids.return_value = {self.product.id: self.product}

    @pytest.mark.asyncio
    async def test_update_item_quantity(self):
        """Test updating an item's quantity."""
        use_case = UpdateCartItemUseCase(
            cart_repository=self.cart_repo,
            product_repository=self.product_repo,
        )

        dto = UpdateCartItemDTO(quantity=5)
        result = await use_case.execute(
            store_id=self.store_id,
            customer_id=self.customer_id,
            tenant_id=self.tenant_id,
            item_id=self.cart_item.id,
            dto=dto,
        )

        assert result is not None
        self.cart_repo.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_item_insufficient_stock_raises(self):
        """Test updating to more than available stock raises error."""
        self.product.quantity = 3

        use_case = UpdateCartItemUseCase(
            cart_repository=self.cart_repo,
            product_repository=self.product_repo,
        )

        dto = UpdateCartItemDTO(quantity=10)
        with pytest.raises(InsufficientStockError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                tenant_id=self.tenant_id,
                item_id=self.cart_item.id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_update_item_not_found_raises(self):
        """Test updating a non-existent item raises error."""
        use_case = UpdateCartItemUseCase(
            cart_repository=self.cart_repo,
            product_repository=self.product_repo,
        )

        dto = UpdateCartItemDTO(quantity=2)
        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                tenant_id=self.tenant_id,
                item_id=uuid4(),
                dto=dto,
            )


class TestRemoveCartItemUseCase:
    """Tests for RemoveCartItemUseCase."""

    @pytest.mark.asyncio
    async def test_remove_item(self):
        """Test removing an item from the cart."""
        store_id = uuid4()
        customer_id = uuid4()
        tenant_id = uuid4()
        product = make_product(store_id)
        cart = make_cart(store_id, customer_id)
        item = cart.add_item(product_id=product.id, quantity=2)

        cart_repo = AsyncMock()
        product_repo = AsyncMock()
        cart_repo.get_active_cart.return_value = cart
        cart_repo.get_or_create_cart.return_value = cart
        cart_repo.update.return_value = cart
        product_repo.get_by_id.return_value = product
        product_repo.get_by_ids.return_value = {product.id: product}

        use_case = RemoveCartItemUseCase(
            cart_repository=cart_repo,
            product_repository=product_repo,
        )

        result = await use_case.execute(
            store_id=store_id,
            customer_id=customer_id,
            tenant_id=tenant_id,
            item_id=item.id,
        )

        assert result is not None
        cart_repo.update.assert_called_once()


class TestClearCartUseCase:
    """Tests for ClearCartUseCase."""

    @pytest.mark.asyncio
    async def test_clear_cart(self):
        """Test clearing the cart."""
        store_id = uuid4()
        customer_id = uuid4()
        cart = make_cart(store_id, customer_id)
        cart.add_item(product_id=uuid4(), quantity=2)

        cart_repo = AsyncMock()
        cart_repo.get_active_cart.return_value = cart

        use_case = ClearCartUseCase(cart_repository=cart_repo)
        await use_case.execute(store_id=store_id, customer_id=customer_id)

        cart_repo.clear_cart.assert_called_once_with(cart.id)

    @pytest.mark.asyncio
    async def test_clear_cart_not_found_raises(self):
        """Test clearing a non-existent cart raises error."""
        cart_repo = AsyncMock()
        cart_repo.get_active_cart.return_value = None

        use_case = ClearCartUseCase(cart_repository=cart_repo)

        with pytest.raises(EntityNotFoundError):
            await use_case.execute(store_id=uuid4(), customer_id=uuid4())


# ============================================================================
# Checkout Use Case Tests
# ============================================================================


class TestCheckoutUseCase:
    """Tests for CheckoutUseCase."""

    def setup_method(self):
        self.store_id = uuid4()
        self.customer_id = uuid4()
        self.tenant_id = uuid4()

        self.store = make_store(store_id=self.store_id)
        self.product1 = make_product(self.store_id, price_cents=10000, quantity=50)
        self.product2 = make_product(self.store_id, price_cents=5000, quantity=20)
        self.product2.name = "Product 2"
        self.product2.slug = "product-2"

        self.cart = make_cart(self.store_id, self.customer_id)
        self.cart.add_item(product_id=self.product1.id, quantity=2)
        self.cart.add_item(product_id=self.product2.id, quantity=1)

        # Mock repositories
        self.cart_repo = AsyncMock()
        self.order_repo = AsyncMock()
        self.product_repo = AsyncMock()
        self.store_repo = AsyncMock()

        self.cart_repo.get_active_cart.return_value = self.cart
        self.store_repo.get_by_id.return_value = self.store
        self.order_repo.get_next_order_number.return_value = "ORD-000001"

        # Return the correct product for each ID
        async def get_product(product_id):
            if product_id == self.product1.id:
                return self.product1
            if product_id == self.product2.id:
                return self.product2
            return None

        self.product_repo.get_by_id.side_effect = get_product

        # Make order_repo.create return the order it receives
        async def create_order(order):
            return order

        self.order_repo.create.side_effect = create_order

    def _make_checkout_dto(self, **kwargs) -> CheckoutDTO:
        defaults = {
            "shipping_address": make_checkout_address(),
            "currency": "EGP",
        }
        defaults.update(kwargs)
        return CheckoutDTO(**defaults)

    @pytest.mark.asyncio
    async def test_successful_checkout(self):
        """Test full checkout flow creates order and deducts inventory."""
        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto()
        result = await use_case.execute(
            store_id=self.store_id,
            customer_id=self.customer_id,
            dto=dto,
        )

        # Order created with correct data
        assert result.order_number == "ORD-000001"
        assert result.status == OrderStatus.PENDING.value
        assert result.payment_status == PaymentStatus.PENDING.value
        assert len(result.line_items) == 2

        # Subtotal: (10000 * 2) + (5000 * 1) = 25000
        assert result.subtotal == 25000
        assert result.total == 25000

        # Inventory deducted
        self.product_repo.bulk_update_quantity.assert_called_once()
        updates = self.product_repo.bulk_update_quantity.call_args[0][0]
        assert len(updates) == 2

        # Cart cleared
        self.cart_repo.clear_cart.assert_called_once_with(self.cart.id)

    @pytest.mark.asyncio
    async def test_checkout_with_shipping_and_tax(self):
        """Test checkout includes shipping and tax in total."""
        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto(
            shipping_cost=5000,
            tax_amount=3500,
            discount_amount=1000,
        )
        result = await use_case.execute(
            store_id=self.store_id,
            customer_id=self.customer_id,
            dto=dto,
        )

        # Total = 25000 + 5000 + 3500 - 1000 = 32500
        assert result.subtotal == 25000
        assert result.shipping_cost == 5000
        assert result.tax_amount == 3500
        assert result.discount_amount == 1000
        assert result.total == 32500

    @pytest.mark.asyncio
    async def test_checkout_empty_cart_raises(self):
        """Test checkout with empty cart raises validation error."""
        empty_cart = make_cart(self.store_id, self.customer_id)
        self.cart_repo.get_active_cart.return_value = empty_cart

        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto()
        with pytest.raises(ValidationError, match="empty"):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_checkout_no_cart_raises(self):
        """Test checkout with no cart raises validation error."""
        self.cart_repo.get_active_cart.return_value = None

        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto()
        with pytest.raises(ValidationError, match="empty"):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_checkout_insufficient_stock_raises(self):
        """Test checkout with insufficient stock raises error."""
        self.product1.quantity = 1  # Only 1 available, cart has 2

        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto()
        with pytest.raises(InsufficientStockError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_checkout_product_not_found_raises(self):
        """Test checkout when product was deleted raises error."""
        self.product_repo.get_by_id.side_effect = None
        self.product_repo.get_by_id.return_value = None

        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto()
        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_checkout_store_not_found_raises(self):
        """Test checkout when store doesn't exist raises error."""
        self.store_repo.get_by_id.return_value = None

        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto()
        with pytest.raises(EntityNotFoundError):
            await use_case.execute(
                store_id=self.store_id,
                customer_id=self.customer_id,
                dto=dto,
            )

    @pytest.mark.asyncio
    async def test_checkout_uses_current_product_prices(self):
        """Test checkout resolves prices at checkout time, not cart time."""
        # Simulate price change after add-to-cart
        self.product1.price = Money(amount=Decimal("15000"), currency=Currency.EGP)

        use_case = CheckoutUseCase(
            cart_repository=self.cart_repo,
            order_repository=self.order_repo,
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

        dto = self._make_checkout_dto()
        result = await use_case.execute(
            store_id=self.store_id,
            customer_id=self.customer_id,
            dto=dto,
        )

        # New price: (15000 * 2) + (5000 * 1) = 35000
        assert result.subtotal == 35000
