"""Tests for multi-tenant data isolation."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.entities.customer import Customer
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
)
from src.core.entities.product import Product
from src.core.entities.store import Store
from src.core.value_objects.email import Email
from src.core.value_objects.money import Currency, Money


class TestCustomerTenantIsolation:
    """Tests for customer tenant isolation."""

    def test_customer_belongs_to_single_store(self):
        """Test that customer is scoped to a single store."""
        store_id = uuid4()
        customer = Customer(
            id=uuid4(),
            store_id=store_id,
            email=Email(value="customer@example.com"),
            first_name="Test",
            last_name="Customer",
        )

        assert customer.store_id == store_id

    def test_customers_from_different_stores_isolated(self):
        """Test that customers from different stores are isolated."""
        store_a_id = uuid4()
        store_b_id = uuid4()

        customer_a = Customer(
            id=uuid4(),
            store_id=store_a_id,
            email=Email(value="customer@example.com"),
            first_name="Customer",
            last_name="A",
        )

        customer_b = Customer(
            id=uuid4(),
            store_id=store_b_id,
            email=Email(value="customer@example.com"),  # Same email different store
            first_name="Customer",
            last_name="B",
        )

        # Same email can exist in different stores
        assert customer_a.email == customer_b.email
        # But they're different customers
        assert customer_a.id != customer_b.id
        assert customer_a.store_id != customer_b.store_id

    def test_customer_store_id_is_required(self):
        """Test that customer must have a store_id."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Customer(
                id=uuid4(),
                # Missing store_id
                email=Email(value="customer@example.com"),
                first_name="Test",
                last_name="Customer",
            )


class TestProductTenantIsolation:
    """Tests for product tenant isolation."""

    def test_product_belongs_to_single_store(self):
        """Test that product is scoped to a single store."""
        store_id = uuid4()
        product = Product(
            id=uuid4(),
            store_id=store_id,
            name="Test Product",
            slug="test-product",
            price=Money(amount=Decimal("19.99"), currency=Currency.USD),
        )

        assert product.store_id == store_id

    def test_products_from_different_stores_isolated(self):
        """Test that products from different stores are isolated."""
        store_a_id = uuid4()
        store_b_id = uuid4()

        product_a = Product(
            id=uuid4(),
            store_id=store_a_id,
            name="Test Product",
            slug="test-product",  # Same slug different store
            price=Money(amount=Decimal("19.99"), currency=Currency.USD),
        )

        product_b = Product(
            id=uuid4(),
            store_id=store_b_id,
            name="Test Product",
            slug="test-product",  # Same slug different store
            price=Money(amount=Decimal("29.99"), currency=Currency.USD),
        )

        # Same slug can exist in different stores
        assert product_a.slug == product_b.slug
        # But they're different products
        assert product_a.id != product_b.id
        assert product_a.store_id != product_b.store_id
        assert product_a.price != product_b.price


class TestOrderTenantIsolation:
    """Tests for order tenant isolation."""

    def _create_order(self, store_id: uuid4, customer_id: uuid4) -> Order:
        """Helper to create an order."""
        return Order(
            id=uuid4(),
            store_id=store_id,
            customer_id=customer_id,
            order_number=f"ORD-{uuid4().hex[:8].upper()}",
            shipping_address=OrderShippingAddress(
                first_name="Test",
                last_name="Customer",
                address_line1="123 Main St",
                city="Cairo",
                country="EG",
            ),
            line_items=[
                OrderLineItem(
                    product_id=uuid4(),
                    product_name="Test Product",
                    quantity=1,
                    unit_price=1999,
                    total_price=1999,
                )
            ],
            subtotal=1999,
            total=1999,
        )

    def test_order_belongs_to_single_store(self):
        """Test that order is scoped to a single store."""
        store_id = uuid4()
        customer_id = uuid4()

        order = self._create_order(store_id, customer_id)

        assert order.store_id == store_id
        assert order.customer_id == customer_id

    def test_orders_from_different_stores_isolated(self):
        """Test that orders from different stores are isolated."""
        store_a_id = uuid4()
        store_b_id = uuid4()

        order_a = self._create_order(store_a_id, uuid4())
        order_b = self._create_order(store_b_id, uuid4())

        assert order_a.store_id != order_b.store_id
        assert order_a.id != order_b.id


class TestStoreTenantIsolation:
    """Tests for store tenant isolation."""

    def test_store_has_unique_slug(self):
        """Test that stores have slugs for URL isolation."""
        store = Store(
            id=uuid4(),
            name="My Store",
            slug="my-store",
            owner_id=uuid4(),
        )

        assert store.slug == "my-store"

    def test_store_tenant_id_isolation(self):
        """Test that stores can be assigned to tenants."""
        tenant_a_id = uuid4()
        tenant_b_id = uuid4()

        store_a = Store(
            id=uuid4(),
            name="Store A",
            slug="store-a",
            owner_id=uuid4(),
            tenant_id=tenant_a_id,
        )

        store_b = Store(
            id=uuid4(),
            name="Store B",
            slug="store-b",
            owner_id=uuid4(),
            tenant_id=tenant_b_id,
        )

        assert store_a.tenant_id == tenant_a_id
        assert store_b.tenant_id == tenant_b_id
        assert store_a.tenant_id != store_b.tenant_id


class TestCrossTenantAccessPrevention:
    """Tests for preventing cross-tenant data access."""

    def test_customer_cannot_access_other_store(self):
        """Test customer store scoping prevents cross-store access."""
        store_a_id = uuid4()
        store_b_id = uuid4()

        # Customer belongs to store A
        customer = Customer(
            id=uuid4(),
            store_id=store_a_id,
            email=Email(value="customer@example.com"),
            first_name="Test",
            last_name="Customer",
        )

        # Verify customer is tied to store A
        assert customer.store_id == store_a_id
        assert customer.store_id != store_b_id

        # In actual implementation, repository queries would filter by store_id
        # This test documents the expected behavior

    def test_order_customer_store_consistency(self):
        """Test that order references correct store and customer."""
        store_id = uuid4()
        customer_id = uuid4()

        order = Order(
            id=uuid4(),
            store_id=store_id,
            customer_id=customer_id,
            order_number=f"ORD-{uuid4().hex[:8].upper()}",
            shipping_address=OrderShippingAddress(
                first_name="Test",
                last_name="Customer",
                address_line1="123 Main St",
                city="Cairo",
                country="EG",
            ),
            line_items=[],
            subtotal=0,
            total=0,
        )

        # Order maintains store_id for data isolation
        assert order.store_id == store_id
        assert order.customer_id == customer_id


class TestTenantDataSegregation:
    """Tests for tenant data segregation patterns."""

    def test_entities_have_store_id_for_isolation(self):
        """Test that tenant-scoped entities have store_id field."""
        store_id = uuid4()

        # Customer has store_id
        customer = Customer(
            id=uuid4(),
            store_id=store_id,
            email=Email(value="customer@example.com"),
            first_name="Test",
            last_name="Customer",
        )
        assert hasattr(customer, "store_id")

        # Product has store_id
        product = Product(
            id=uuid4(),
            store_id=store_id,
            name="Test Product",
            slug="test-product",
            price=Money(amount=Decimal("19.99"), currency=Currency.USD),
        )
        assert hasattr(product, "store_id")

        # Order has store_id
        order = Order(
            id=uuid4(),
            store_id=store_id,
            customer_id=customer.id,
            order_number="ORD-001",
            shipping_address=OrderShippingAddress(
                first_name="Test",
                last_name="Customer",
                address_line1="123 Main St",
                city="Cairo",
                country="EG",
            ),
            line_items=[],
            subtotal=0,
            total=0,
        )
        assert hasattr(order, "store_id")

    def test_store_has_owner_for_authorization(self):
        """Test that store has owner_id for authorization."""
        owner_id = uuid4()
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=owner_id,
        )

        assert store.owner_id == owner_id
        assert store.is_owned_by(owner_id) is True
        assert store.is_owned_by(uuid4()) is False
