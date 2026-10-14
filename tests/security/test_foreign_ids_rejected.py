"""Ids taken from a merchant's request body must belong to the path store.

An order created with another store's customer_id echoed that customer's name
and email back; bundles and upsell offers pointing at another store's product
render its name, price and stock on the merchant's storefront.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.dto.order import CreateOrderDTO
from src.application.use_cases.orders.create_order import CreateOrderUseCase
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories.product_bundle_repository import (
    ProductBundleRepository,
)
from src.infrastructure.repositories.upsell_rule_repository import (
    UpsellRuleRepository,
)


class _CountSession:
    """`session.scalar(count …)` answers how many of the ids are the store's."""

    def __init__(self, owned: int) -> None:
        self.owned = owned
        self.added = []

    async def scalar(self, query):  # noqa: ARG002
        return self.owned

    def add(self, model):
        self.added.append(model)

    async def flush(self):
        pass


async def test_order_refuses_another_stores_customer():
    store_id, owner = uuid4(), uuid4()

    class _Stores:
        async def get_by_id(self, sid):
            return SimpleNamespace(id=store_id, owner_id=owner)

    class _Customers:
        async def get_by_id(self, cid):
            return SimpleNamespace(id=cid, store_id=uuid4())

    class _Orders:
        def __getattr__(self, name):
            raise AssertionError(f"order created for a foreign customer: {name}")

    use_case = CreateOrderUseCase(_Orders(), _Stores(), _Customers())
    dto = CreateOrderDTO(customer_id=uuid4(), line_items=[], shipping_address=None)

    with pytest.raises(EntityNotFoundError):
        await use_case.execute(dto, store_id, owner)


async def test_bundle_refuses_a_foreign_product():
    session = _CountSession(owned=1)  # primary is ours, bundled is not

    with pytest.raises(EntityNotFoundError):
        await ProductBundleRepository(session).create({
            "store_id": uuid4(),
            "primary_product_id": uuid4(),
            "bundled_product_id": uuid4(),
        })
    assert session.added == []


async def test_bundle_bulk_create_refuses_a_foreign_product():
    session = _CountSession(owned=1)

    with pytest.raises(EntityNotFoundError):
        await ProductBundleRepository(session).bulk_create([
            {
                "store_id": uuid4(),
                "primary_product_id": uuid4(),
                "bundled_product_id": uuid4(),
            }
        ])


async def test_upsell_refuses_a_foreign_offer_product():
    repo = UpsellRuleRepository(_CountSession(owned=0))

    with pytest.raises(EntityNotFoundError):
        await repo.create({"store_id": uuid4(), "offer_product_id": uuid4()})
    with pytest.raises(EntityNotFoundError):
        await repo.update(SimpleNamespace(store_id=uuid4(), offer_product_id=uuid4()))


async def test_upsell_accepts_its_own_offer_product():
    session = _CountSession(owned=1)
    await UpsellRuleRepository(session).create({
        "store_id": uuid4(),
        "name": "x",
        "offer_product_id": uuid4(),
    })
    assert len(session.added) == 1
