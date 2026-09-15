"""Storefront search: author / series / ISBN matching and the grouped response."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.api.v1.routes.storefront import search as search_routes
from src.api.v1.routes.storefront.search import (
    DEFAULT_TYPES,
    _normalize_isbn,
    _product_search,
)


def _sql(clause):
    compiled = clause.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("978-0-13-468599-1", "9780134685991"),
        (" 9780134685991 ", "9780134685991"),
        ("0-306-40615-2", "0306406152"),
        ("080442957x", "080442957X"),
        ("12345", None),
        ("97801346859912", None),
        ("schwab", None),
        ("978 0134685991", None),
        ("X-X-X", None),
        ("", None),
    ],
)
def test_normalize_isbn(raw, expected):
    assert _normalize_isbn(raw) == expected


def test_blank_query_has_no_filter():
    assert _product_search(uuid4(), "   ") is None


def test_product_filter_matches_tsvector_author_and_series():
    where, order = _product_search(uuid4(), "schwab")
    sql, params = _sql(where)
    assert "@@ to_tsquery" in sql
    assert "->>" in sql
    assert "series" in sql
    assert "product_variants" not in sql
    assert "schwab" in params.values()
    assert "@@" in _sql(order[0])[0]


def test_ilike_input_is_escaped():
    where, _ = _product_search(uuid4(), "50%_off")
    sql, params = _sql(where)
    assert "ESCAPE '/'" in sql
    assert "50/%/_off" in params.values()


def test_isbn_query_matches_attribute_and_skus():
    where, _ = _product_search(uuid4(), "978-0-13-468599-1")
    sql, params = _sql(where)
    assert "product_variants" in sql
    assert "replace" in sql
    assert list(params.values()).count("9780134685991") == 3


class _Store:
    async def get_by_id(self, _):
        return object()


async def test_predictive_returns_author_and_series_groups(monkeypatch):
    product = SimpleNamespace(
        id=uuid4(),
        name="COVID-19: The Great Reset",
        slug="covid-19-the-great-reset",
        sku=None,
        price_amount=35000,
        compare_at_price=None,
        price_currency="EGP",
        images=[],
        tags=[],
        attributes={"author": "Klaus Schwab"},
        quantity=3,
        low_stock_threshold=5,
    )

    async def products(*_, **__):
        return [product]

    async def authors(*_, **__):
        return [{"name": "Klaus Schwab", "product_count": 1}]

    async def series(*_, **__):
        return []

    monkeypatch.setattr(search_routes, "_search_products", products)
    monkeypatch.setattr(search_routes, "_search_authors", authors)
    monkeypatch.setattr(search_routes, "_search_series", series)

    response = await search_routes.predictive_search(
        store_id=uuid4(),
        session=None,
        store_repo=_Store(),
        category_repo=None,
        q="schwab",
        types="products,authors,series",
        limit=5,
    )

    data = response.data
    assert data["authors"] == [{"name": "Klaus Schwab", "product_count": 1}]
    assert data["series"] == []
    assert data["products"][0]["is_low_stock"] is True
    assert data["total"] == 1
    assert {"authors", "series"} <= set(DEFAULT_TYPES.split(","))
