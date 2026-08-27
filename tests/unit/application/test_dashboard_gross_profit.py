"""Unit tests for the dashboard gross-profit aggregate.

The tile used to be labelled "Net Profit" and disagreed with the Sales
tile sitting next to it, for four independent reasons. Each is pinned
here so none can quietly come back:

* **Order set** — profit filtered on ``payment_status == PAID`` while
  revenue filtered on ``exclude_non_revenue(status)``. On a COD store
  most orders never reach PAID, so the two tiles described different
  order sets and could not be reconciled by construction.
* **Variant cost** — only ``product.cost_price`` was read, so a product
  costed per-SKU in the variant editor was dropped from the maths *and*
  from the "N of M products have a cost set" hint.
* **Line discounts** — the line was valued at ``unit_price`` (list
  price) rather than ``total_price``, so every markdown inflated profit.
* **Order discounts** — coupons live on the order and were never
  subtracted at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.application.use_cases.stores.get_dashboard_stats import (
    GetDashboardStatsUseCase,
)
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.entities.product import Product, ProductStatus
from src.core.entities.store import Store
from src.core.value_objects.money import Currency, Money

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
PERIOD_START = NOW - timedelta(days=30)

STORE_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()

ADDRESS = OrderShippingAddress(
    first_name="Yara",
    last_name="Hassan",
    address_line1="1 Nile St",
    city="Cairo",
    country="EG",
)


def _money(major: str) -> Money:
    return Money(amount=major, currency=Currency.EGP)


def _product(cost_major: str | None) -> Product:
    return Product(
        id=uuid.uuid4(),
        store_id=STORE_ID,
        name="Abaya",
        slug=f"abaya-{uuid.uuid4().hex[:8]}",
        price=_money("500"),
        cost_price=_money(cost_major) if cost_major is not None else None,
        status=ProductStatus.ACTIVE,
    )


class _Variant:
    """Minimal stand-in — the use case only reads ``id`` / ``cost_price``."""

    def __init__(self, cost_major: str | None) -> None:
        self.id = uuid.uuid4()
        self.cost_price = _money(cost_major) if cost_major is not None else None


def _line(
    product_id: uuid.UUID,
    *,
    unit_price: int,
    quantity: int = 1,
    total_price: int | None = None,
    variant_id: uuid.UUID | None = None,
) -> OrderLineItem:
    return OrderLineItem(
        product_id=product_id,
        product_name="Abaya",
        variant_id=variant_id,
        quantity=quantity,
        unit_price=unit_price,
        total_price=unit_price * quantity if total_price is None else total_price,
    )


def _order(
    *,
    line_items: list[OrderLineItem],
    status: OrderStatus = OrderStatus.PENDING,
    payment_status: PaymentStatus = PaymentStatus.PENDING,
    subtotal: int = 0,
    discount_amount: int = 0,
) -> Order:
    gross = subtotal or sum(li.total_price for li in line_items)
    return Order(
        id=uuid.uuid4(),
        store_id=STORE_ID,
        customer_id=uuid.uuid4(),
        order_number="ORD-123456",
        line_items=line_items,
        shipping_address=ADDRESS,
        status=status,
        payment_status=payment_status,
        subtotal=gross,
        discount_amount=discount_amount,
        total=gross - discount_amount,
        currency="EGP",
    )


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeOrderRepo:
    def __init__(self, orders: list[Order]) -> None:
        self._orders = orders

    async def get_revenue_by_date_range(self, *_a, **_k) -> int:
        return 0

    async def count_by_store(self, *_a, **_k) -> int:
        return len(self._orders)

    async def get_by_date_range(self, *_a, **_k) -> list[Order]:
        return self._orders


class _FakeCustomerRepo:
    async def count_by_store(self, *_a, **_k) -> int:
        return 0


class _FakeProductRepo:
    def __init__(self, products: list[Product]) -> None:
        self._products = products

    async def count_by_store(self, *_a, **_k) -> int:
        return len(self._products)

    async def get_low_stock(self, *_a, **_k) -> list[Product]:
        return []

    async def get_by_store(self, *_a, **_k) -> list[Product]:
        return self._products


class _FakeStoreRepo:
    async def get_by_id(self, *_a, **_k) -> Store:
        return Store(
            id=STORE_ID,
            owner_id=OWNER_ID,
            name="Qandeel",
            slug="qandeel",
            default_currency="EGP",
        )


class _FakeVariantRepo:
    def __init__(self, by_product: dict[uuid.UUID, list[_Variant]]) -> None:
        self._by_product = by_product

    async def list_for_products(self, product_ids):
        return {pid: self._by_product.get(pid, []) for pid in product_ids}


async def _run(orders, products, variants_by_product=None):
    use_case = GetDashboardStatsUseCase(
        order_repository=_FakeOrderRepo(orders),
        customer_repository=_FakeCustomerRepo(),
        product_repository=_FakeProductRepo(products),
        store_repository=_FakeStoreRepo(),
        variant_repository=(
            _FakeVariantRepo(variants_by_product)
            if variants_by_product is not None
            else None
        ),
    )
    return await use_case.execute(
        store_id=STORE_ID,
        user_id=OWNER_ID,
        period_start=PERIOD_START,
        period_end=NOW,
    )


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unpaid_cod_order_counts_toward_profit():
    """The regression that started this: a COD order sitting at
    payment_status PENDING is revenue, so it must also be profit."""
    product = _product("300")  # cost EGP 300 => 30_000 cents
    order = _order(
        line_items=[_line(product.id, unit_price=50_000)],
        status=OrderStatus.CONFIRMED,
        payment_status=PaymentStatus.PENDING,
    )

    stats = await _run([order], [product])

    assert stats.total_cogs == 30_000
    assert stats.total_profit == 20_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
        OrderStatus.DRAFT,
        OrderStatus.PAYMENT_FAILED,
    ],
)
async def test_non_revenue_statuses_are_excluded(status):
    """The same four statuses the Sales tile drops — no more, no fewer."""
    product = _product("300")
    order = _order(
        line_items=[_line(product.id, unit_price=50_000)],
        status=status,
        payment_status=PaymentStatus.PAID,
    )

    stats = await _run([order], [product])

    assert stats.total_profit == 0
    assert stats.total_cogs == 0


@pytest.mark.asyncio
async def test_returned_order_still_counts():
    """RETURNED deliberately stays in booked revenue (see
    ``order_status_filters``), so profit has to agree with revenue."""
    product = _product("300")
    order = _order(
        line_items=[_line(product.id, unit_price=50_000)],
        status=OrderStatus.RETURNED,
    )

    stats = await _run([order], [product])

    assert stats.total_profit == 20_000


@pytest.mark.asyncio
async def test_variant_cost_wins_over_product_cost():
    product = _product("300")  # parent cost 30_000 cents
    variant = _Variant("400")  # variant cost 40_000 cents
    order = _order(
        line_items=[_line(product.id, unit_price=50_000, variant_id=variant.id)],
    )

    stats = await _run([order], [product], {product.id: [variant]})

    assert stats.total_cogs == 40_000
    assert stats.total_profit == 10_000


@pytest.mark.asyncio
async def test_variant_only_cost_is_not_dropped():
    """A product costed solely on its variants was invisible to both the
    profit maths and the coverage hint."""
    product = _product(None)
    variant = _Variant("400")
    order = _order(
        line_items=[_line(product.id, unit_price=50_000, variant_id=variant.id)],
    )

    stats = await _run([order], [product], {product.id: [variant]})

    assert stats.total_cogs == 40_000
    assert stats.total_profit == 10_000
    assert stats.products_with_cost == 1


@pytest.mark.asyncio
async def test_line_discount_is_respected():
    """``total_price`` is post-discount; ``unit_price`` is the list price
    the merchant never actually charged."""
    product = _product("300")
    order = _order(
        line_items=[
            _line(product.id, quantity=2, unit_price=50_000, total_price=80_000)
        ],
    )

    stats = await _run([order], [product])

    assert stats.total_cogs == 60_000
    # 80_000 - 60_000, not the 40_000 the list price would have claimed.
    assert stats.total_profit == 20_000


@pytest.mark.asyncio
async def test_order_level_discount_is_allocated_pro_rata():
    """A coupon lives on the order; each line absorbs its share of it."""
    cheap = _product("100")
    dear = _product("100")
    order = _order(
        line_items=[
            _line(cheap.id, unit_price=25_000),
            _line(dear.id, unit_price=75_000),
        ],
        subtotal=100_000,
        discount_amount=10_000,  # 10% off the whole order
    )

    stats = await _run([order], [cheap, dear])

    assert stats.total_cogs == 20_000
    # 22_500 + 67_500 = 90_000 of revenue, less 20_000 of cost.
    assert stats.total_profit == 70_000


@pytest.mark.asyncio
async def test_uncosted_products_are_excluded_from_both_totals():
    costed = _product("300")
    uncosted = _product(None)
    order = _order(
        line_items=[
            _line(costed.id, unit_price=50_000),
            _line(uncosted.id, unit_price=90_000),
        ],
    )

    stats = await _run([order], [costed, uncosted])

    assert stats.total_cogs == 30_000
    assert stats.total_profit == 20_000
    assert stats.products_with_cost == 1
    assert stats.total_products == 2
