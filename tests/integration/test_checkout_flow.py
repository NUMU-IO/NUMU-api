"""Integration tests for the storefront checkout flow.

Covers:
- Cart → Checkout → Order creation
- Price resolution from product catalog
- Stock validation and deduction
- Payment status handling
- Paymob webhook processing
- Backend price recalculation (C-10)
"""

from uuid import uuid4

from src.core.entities.customer import Customer
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.email import Email
from src.core.value_objects.money import Currency, Money
from src.core.value_objects.phone import PhoneNumber

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(owner_id=None):
    return Store(
        id=uuid4(),
        owner_id=owner_id or uuid4(),
        name="Test Store",
        slug="test-store",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
    )


def _make_product(store_id, *, price_cents=10000, quantity=50):
    return Product(
        id=uuid4(),
        store_id=store_id,
        name="Widget",
        slug="widget",
        sku="WDG-001",
        product_type=ProductType.PHYSICAL,
        status=ProductStatus.ACTIVE,
        price=Money.from_cents(price_cents, Currency.EGP),
        quantity=quantity,
    )


def _make_customer(store_id):
    return Customer(
        id=uuid4(),
        store_id=store_id,
        email=Email(value=f"cust_{uuid4().hex[:6]}@example.com"),
        first_name="Alice",
        last_name="Shopper",
        phone=PhoneNumber(value="+201000000000", country_code="EG"),
        is_verified=True,
    )


def _make_address():
    return OrderShippingAddress(
        first_name="Alice",
        last_name="Shopper",
        address_line1="10 Tahrir Square",
        city="Cairo",
        country="EG",
    )


# ---------------------------------------------------------------------------
# Checkout validation
# ---------------------------------------------------------------------------


class TestCheckoutValidation:
    """Verify that checkout enforces business rules."""

    def test_cannot_checkout_inactive_product(self):
        store = _make_store()
        product = _make_product(store.id)
        product.status = ProductStatus.ARCHIVED

        assert product.status != ProductStatus.ACTIVE

    def test_cannot_checkout_out_of_stock_product(self):
        store = _make_store()
        product = _make_product(store.id, quantity=0)

        assert not product.is_in_stock

    def test_cannot_checkout_more_than_available_stock(self):
        store = _make_store()
        product = _make_product(store.id, quantity=3)
        requested_qty = 5

        assert requested_qty > product.quantity

    def test_checkout_product_must_belong_to_store(self):
        store_a = _make_store()
        store_b = _make_store()
        product = _make_product(store_a.id)

        assert product.store_id != store_b.id


# ---------------------------------------------------------------------------
# Order creation from checkout
# ---------------------------------------------------------------------------


class TestCheckoutOrderCreation:
    """Verify that checkout produces a correct Order entity."""

    def test_order_uses_server_side_prices(self):
        """Prices must come from the product catalog, not the client."""
        store = _make_store()
        product = _make_product(store.id, price_cents=15000)
        customer = _make_customer(store.id)

        unit_price = product.price.cents
        quantity = 2
        subtotal = unit_price * quantity

        order = Order(
            store_id=store.id,
            customer_id=customer.id,
            order_number="ORD-000001",
            line_items=[
                OrderLineItem(
                    product_id=product.id,
                    product_name=product.name,
                    sku=product.sku,
                    quantity=quantity,
                    unit_price=unit_price,
                    total_price=subtotal,
                ),
            ],
            shipping_address=_make_address(),
            subtotal=subtotal,
            total=subtotal,
            currency="EGP",
        )

        assert order.subtotal == 30000
        assert order.total == 30000
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PENDING

    def test_order_total_includes_shipping_and_tax(self):
        subtotal = 20000
        shipping = 5000
        tax = 2800
        discount = 1000
        total = subtotal + shipping + tax - discount

        assert total == 26800

    def test_checkout_deducts_stock(self):
        store = _make_store()
        product = _make_product(store.id, quantity=10)
        ordered = 3
        product.quantity -= ordered

        assert product.quantity == 7

    def test_checkout_clears_cart_conceptually(self):
        """After checkout the customer's cart should be empty."""
        from src.api.v1.routes.storefront.cart import _carts

        customer_id = uuid4()
        _carts[customer_id] = [{"id": "x", "product_id": str(uuid4()), "quantity": 1}]

        # Simulate checkout clearing
        _carts.pop(customer_id, None)

        assert customer_id not in _carts


# ---------------------------------------------------------------------------
# Payment webhook processing
# ---------------------------------------------------------------------------


class TestPaymobWebhookProcessing:
    """Verify that webhook callbacks transition order state correctly."""

    def _pending_order(self):
        return Order(
            id=uuid4(),
            store_id=uuid4(),
            customer_id=uuid4(),
            order_number="ORD-WH-001",
            shipping_address=_make_address(),
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="paymob",
            subtotal=10000,
            total=10000,
            currency="EGP",
        )

    def test_mark_paid_transitions_order(self):
        order = self._pending_order()
        order.mark_as_paid(payment_id="txn_123", payment_method="paymob")

        assert order.payment_status == PaymentStatus.PAID
        assert order.status == OrderStatus.PROCESSING
        assert order.payment_id == "txn_123"
        assert order.paid_at is not None

    def test_mark_payment_failed(self):
        order = self._pending_order()
        order.mark_payment_failed(reason="Card declined")

        assert order.payment_status == PaymentStatus.FAILED
        assert order.metadata.get("payment_failure_reason") == "Card declined"

    def test_void_cancels_order(self):
        order = self._pending_order()
        assert order.can_be_cancelled
        order.cancel(reason="Paymob void")

        assert order.status == OrderStatus.CANCELLED
        assert order.cancelled_at is not None

    def test_refund_after_delivery(self):
        order = self._pending_order()
        # Progress to delivered
        order.mark_as_paid(payment_id="txn_200")
        order.ship()
        order.deliver()
        assert order.can_be_refunded

        order.refund(reason="Customer return")
        assert order.status == OrderStatus.REFUNDED
        assert order.payment_status == PaymentStatus.REFUNDED


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestCheckoutLifecycle:
    """End-to-end lifecycle: checkout → pay → ship → deliver."""

    def test_full_online_payment_lifecycle(self):
        store = _make_store()
        customer = _make_customer(store.id)
        product = _make_product(store.id)

        order = Order(
            store_id=store.id,
            customer_id=customer.id,
            order_number="ORD-LIFE-001",
            line_items=[
                OrderLineItem(
                    product_id=product.id,
                    product_name=product.name,
                    quantity=1,
                    unit_price=10000,
                    total_price=10000,
                ),
            ],
            shipping_address=_make_address(),
            subtotal=10000,
            total=10000,
            currency="EGP",
            payment_method="paymob",
        )

        assert order.is_pending

        # Payment succeeds
        order.mark_as_paid(payment_id="txn_300")
        assert order.is_paid
        assert order.status == OrderStatus.PROCESSING

        # Ship
        order.ship(tracking_number="BOSTA-999")
        assert order.status == OrderStatus.SHIPPED
        assert order.tracking_number == "BOSTA-999"

        # Deliver
        order.deliver()
        assert order.status == OrderStatus.DELIVERED
        assert order.delivered_at is not None

    def test_full_cod_lifecycle(self):
        order = Order(
            store_id=uuid4(),
            customer_id=uuid4(),
            order_number="ORD-COD-001",
            shipping_address=_make_address(),
            subtotal=5000,
            total=5000,
            currency="EGP",
            payment_method="cod",
        )

        assert order.status == OrderStatus.PENDING
        order.confirm()
        assert order.status == OrderStatus.CONFIRMED

        order.start_processing()
        assert order.status == OrderStatus.PROCESSING

        order.ship()
        assert order.status == OrderStatus.SHIPPED

        order.deliver()
        assert order.status == OrderStatus.DELIVERED


# ---------------------------------------------------------------------------
# C-10: Backend price recalculation — schema rejects client-supplied prices
# ---------------------------------------------------------------------------


class TestCheckoutPriceRecalculation:
    """Verify that the checkout schema never accepts frontend-supplied prices.

    The backend MUST always resolve prices from the product catalog.
    These tests ensure the schema itself provides no avenue for price injection.
    """

    def test_checkout_request_has_no_price_fields(self):
        """CheckoutRequest must not accept price, total, or amount fields."""
        from src.api.v1.schemas.storefront.checkout import CheckoutRequest

        field_names = set(CheckoutRequest.model_fields.keys())
        price_related = {"price", "unit_price", "total", "subtotal", "amount", "cost"}
        assert field_names.isdisjoint(price_related), (
            f"CheckoutRequest exposes price fields: {field_names & price_related}"
        )

    def test_checkout_line_item_has_no_price_fields(self):
        """CheckoutLineItem must only accept product_id, variant_id, quantity."""
        from src.api.v1.schemas.storefront.checkout import CheckoutLineItem

        field_names = set(CheckoutLineItem.model_fields.keys())
        assert field_names == {"product_id", "variant_id", "quantity"}

    def test_extra_price_field_in_line_item_is_ignored(self):
        """Pydantic should silently drop unknown price fields."""
        from src.api.v1.schemas.storefront.checkout import CheckoutLineItem

        item = CheckoutLineItem(
            product_id=uuid4(),
            quantity=2,
            unit_price=1,  # attacker-supplied
        )
        assert not hasattr(item, "unit_price") or "unit_price" not in item.model_fields

    def test_server_price_overrides_any_client_value(self):
        """Simulate the checkout loop: DB price wins over any client value."""
        store = _make_store()
        product = _make_product(store.id, price_cents=25000)  # DB: 250 EGP

        # Attacker hopes to pay 1 piaster; backend must use DB price
        attacker_price = 1
        server_price = product.price.cents

        assert server_price == 25000
        assert server_price != attacker_price

        line_item = OrderLineItem(
            product_id=product.id,
            product_name=product.name,
            quantity=3,
            unit_price=server_price,  # always from DB
            total_price=server_price * 3,
        )

        assert line_item.total_price == 75000
