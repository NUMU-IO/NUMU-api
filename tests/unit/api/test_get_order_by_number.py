"""GET /stores/{id}/orders/{order_ref} — by UUID or by order number.

The merchant hub puts the order NUMBER in its URLs ("/orders/ORD-767567"),
so a refreshed or pasted page has to resolve through here. UUIDs keep working
for every existing link and integration.

The use case is stubbed to stop at the id it was handed: what's under test is
only which order the route resolves, and that the lookup stays inside the
caller's own store.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.v1.routes.stores import orders as orders_module
from src.core.exceptions import EntityNotFoundError


class _Resolved(Exception):
    def __init__(self, order_id):
        self.order_id = order_id


@pytest.fixture(autouse=True)
def _stop_at_the_use_case(monkeypatch):
    async def execute(self, *, order_id, store_id, user_id):
        raise _Resolved(order_id)

    monkeypatch.setattr(orders_module.GetOrderUseCase, "execute", execute)


def _store():
    return SimpleNamespace(id=uuid4(), owner_id=uuid4())


async def _resolve(order_ref, order_repo):
    with pytest.raises(_Resolved) as caught:
        await orders_module.get_order(
            order_ref=order_ref,
            store=_store(),
            order_repo=order_repo,
            store_repo=AsyncMock(),
            product_repo=AsyncMock(),
        )
    return caught.value.order_id


async def test_uuid_resolves_without_a_number_lookup():
    order_id = uuid4()
    repo = AsyncMock()
    assert await _resolve(str(order_id), repo) == order_id
    repo.get_by_order_number.assert_not_called()


async def test_order_number_resolves_to_its_order():
    order_id = uuid4()
    repo = AsyncMock()
    repo.get_by_order_number.return_value = SimpleNamespace(id=order_id)
    assert await _resolve("ORD-767567", repo) == order_id


async def test_number_lookup_is_scoped_to_the_callers_store():
    store = _store()
    repo = AsyncMock()
    repo.get_by_order_number.return_value = SimpleNamespace(id=uuid4())
    with pytest.raises(_Resolved):
        await orders_module.get_order(
            order_ref="ORD-767567",
            store=store,
            order_repo=repo,
            store_repo=AsyncMock(),
            product_repo=AsyncMock(),
        )
    repo.get_by_order_number.assert_awaited_once_with(store.id, "ORD-767567")


async def test_unknown_order_number_is_not_found():
    repo = AsyncMock()
    repo.get_by_order_number.return_value = None
    with pytest.raises(EntityNotFoundError):
        await orders_module.get_order(
            order_ref="ORD-000000",
            store=_store(),
            order_repo=repo,
            store_repo=AsyncMock(),
            product_repo=AsyncMock(),
        )
