"""Variant repository money-convention regression tests.

Locks in the fix where `product_variants.price_amount` is CENTS (same as
`products`), so `Money.amount` is MAJOR units and `.cents` is the column value.
Previously the repo treated the column as MAJOR, which made the storefront
under-read variant prices 100× (220 EGP rendered as 2.20).
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from src.infrastructure.repositories.variant_repository import (
    _money_amount,
    _to_entity,
)


def _mock_row(price_amount: int, compare_at: int | None = None) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.tenant_id = uuid4()
    row.store_id = uuid4()
    row.product_id = uuid4()
    row.position = 0
    row.option_values = {}
    row.price_amount = price_amount
    row.price_currency = "EGP"
    row.compare_at_price = compare_at
    row.cost_price = None
    row.sku = None
    row.barcode = None
    row.inventory_quantity = 5
    row.image_url = None
    row.weight = None
    row.metadata_ = {}
    row.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    row.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return row


def test_read_treats_price_amount_as_cents():
    # column holds CENTS (22000 = EGP 220)
    entity = _to_entity(_mock_row(22000))
    assert entity.price.amount == Decimal("220")  # MAJOR for display
    assert entity.price.cents == 22000  # CENTS for cart/checkout


def test_compare_at_price_is_cents_too():
    entity = _to_entity(_mock_row(22000, compare_at=30000))
    assert entity.compare_at_price is not None
    assert entity.compare_at_price.amount == Decimal("300")
    assert entity.compare_at_price.cents == 30000


def test_money_amount_helper_writes_cents():
    from src.core.value_objects.money import Currency, Money

    # admin builds Money from MAJOR input; helper must persist CENTS
    assert _money_amount(Money(amount=Decimal("220"), currency=Currency.EGP)) == 22000
    assert _money_amount(None) is None
