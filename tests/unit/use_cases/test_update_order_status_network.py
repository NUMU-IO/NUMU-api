"""Unit tests for UpdateOrderStatusUseCase network event firing.

Covers the new behaviour where transitioning a COD order into DELIVERED
or RETURNED writes a `delivery` / `rto` event to the cross-merchant
trust network, idempotent via ``order.metadata`` flags.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.dto.order import UpdateOrderStatusDTO
from src.application.use_cases.orders.update_order_status import (
    UpdateOrderStatusUseCase,
)
from src.core.entities.order import (
    Order,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)


def _build_order(
    *,
    status: OrderStatus = OrderStatus.SHIPPED,
    payment_method: str = "cod",
    metadata: dict | None = None,
) -> Order:
    return Order(
        id=uuid4(),
        store_id=uuid4(),
        tenant_id=uuid4(),
        customer_id=uuid4(),
        order_number="ORD-1234",
        line_items=[],
        shipping_address=OrderShippingAddress(
            first_name="Test",
            last_name="Customer",
            address_line1="1 Test St",
            city="Cairo",
            country="EG",
            phone="+201001234567",
        ),
        status=status,
        payment_status=PaymentStatus.PENDING,
        subtotal=10_000,
        shipping_cost=0,
        tax_amount=0,
        discount_amount=0,
        total=10_000,
        currency="EGP",
        payment_method=payment_method,
        metadata=metadata or {},
    )


def _build_store(*, order_store_id):
    """Minimal store stub matching what the use case reads."""

    class _Store:
        id = order_store_id
        owner_id = uuid4()
        name = "Test Store"
        default_language = "en"

    return _Store()


@pytest.fixture(autouse=True)
def _stub_phone_salt(monkeypatch):
    class _S:
        platform_secret_salt = "test-salt"

    monkeypatch.setattr(
        "src.application.services.network_reputation_service.get_settings",
        lambda: _S(),
    )


@pytest.fixture(autouse=True)
def _stub_redis(monkeypatch):
    """`write_network_event` reaches into Redis to invalidate cache —
    we fake it so tests don't need a real Redis."""

    class _BadRedis:
        def __init__(self, *a, **kw):
            pass

        async def get(self, _key):
            return None

        async def set(self, *_a, **_kw):
            return None

        async def close(self):
            return None

        async def delete(self, _key):
            return None

    monkeypatch.setattr(
        "src.infrastructure.cache.redis_cache.RedisCacheService", _BadRedis
    )


def _build_use_case(*, order, network_repo=None):
    """Build a use case with mocks for everything except the network repo."""
    store = _build_store(order_store_id=order.store_id)

    order_repo = AsyncMock()
    order_repo.get_by_id = AsyncMock(return_value=order)
    order_repo.update = AsyncMock(side_effect=lambda o: o)

    store_repo = AsyncMock()
    store_repo.get_by_id = AsyncMock(return_value=store)

    customer_repo = AsyncMock()
    customer_repo.get_by_id = AsyncMock(return_value=None)

    return (
        UpdateOrderStatusUseCase(
            order_repository=order_repo,
            store_repository=store_repo,
            customer_repository=customer_repo,
            event_bus=None,
            network_repository=network_repo,
        ),
        store,
        order_repo,
    )


@pytest.mark.asyncio
async def test_delivered_cod_fires_delivery_event_and_stamps_flag():
    order = _build_order(status=OrderStatus.SHIPPED, payment_method="cod")
    network_repo = AsyncMock()
    network_repo.upsert_order = AsyncMock()
    network_repo.record_event = AsyncMock()
    network_repo.update_store_count = AsyncMock()
    network_repo.recompute_cached_score = AsyncMock()

    use_case, store, _ = _build_use_case(order=order, network_repo=network_repo)
    await use_case.execute(
        order_id=order.id,
        dto=UpdateOrderStatusDTO(status="delivered"),
        store_id=order.store_id,
        user_id=store.owner_id,
    )

    network_repo.record_event.assert_awaited_once()
    call_kwargs = network_repo.record_event.await_args.kwargs
    assert call_kwargs["event_type"] == "delivery"
    assert order.metadata.get("network_delivery_recorded") is True


@pytest.mark.asyncio
async def test_returned_cod_fires_rto_event_and_stamps_flag():
    order = _build_order(status=OrderStatus.SHIPPED, payment_method="cod")
    network_repo = AsyncMock()
    network_repo.upsert_order = AsyncMock()
    network_repo.record_event = AsyncMock()
    network_repo.update_store_count = AsyncMock()
    network_repo.recompute_cached_score = AsyncMock()

    use_case, store, _ = _build_use_case(order=order, network_repo=network_repo)
    await use_case.execute(
        order_id=order.id,
        dto=UpdateOrderStatusDTO(status="returned", reason="customer refused"),
        store_id=order.store_id,
        user_id=store.owner_id,
    )

    assert order.status == OrderStatus.RETURNED
    network_repo.record_event.assert_awaited_once()
    call_kwargs = network_repo.record_event.await_args.kwargs
    assert call_kwargs["event_type"] == "rto"
    assert order.metadata.get("network_rto_recorded") is True


@pytest.mark.asyncio
async def test_already_recorded_flag_skips_network_event():
    """Idempotency: existing network_*_recorded flag means we don't double-count."""
    order = _build_order(
        status=OrderStatus.SHIPPED,
        payment_method="cod",
        metadata={"network_delivery_recorded": True},
    )
    network_repo = AsyncMock()
    network_repo.record_event = AsyncMock()
    network_repo.upsert_order = AsyncMock()
    network_repo.update_store_count = AsyncMock()
    network_repo.recompute_cached_score = AsyncMock()

    use_case, store, _ = _build_use_case(order=order, network_repo=network_repo)
    await use_case.execute(
        order_id=order.id,
        dto=UpdateOrderStatusDTO(status="delivered"),
        store_id=order.store_id,
        user_id=store.owner_id,
    )

    network_repo.record_event.assert_not_called()


@pytest.mark.asyncio
async def test_non_cod_order_does_not_fire_network_event():
    order = _build_order(status=OrderStatus.SHIPPED, payment_method="paymob_card")
    network_repo = AsyncMock()
    network_repo.record_event = AsyncMock()

    use_case, store, _ = _build_use_case(order=order, network_repo=network_repo)
    await use_case.execute(
        order_id=order.id,
        dto=UpdateOrderStatusDTO(status="delivered"),
        store_id=order.store_id,
        user_id=store.owner_id,
    )

    network_repo.record_event.assert_not_called()
    assert order.metadata.get("network_delivery_recorded") is None


@pytest.mark.asyncio
async def test_no_network_repo_skips_event_silently():
    """Use case is backwards-compatible: callers that don't pass a
    network repo don't hit a NoneType error."""
    order = _build_order(status=OrderStatus.SHIPPED, payment_method="cod")
    use_case, store, _ = _build_use_case(order=order, network_repo=None)
    # No exception expected.
    await use_case.execute(
        order_id=order.id,
        dto=UpdateOrderStatusDTO(status="delivered"),
        store_id=order.store_id,
        user_id=store.owner_id,
    )
    assert order.status == OrderStatus.DELIVERED
    assert order.metadata.get("network_delivery_recorded") is None


@pytest.mark.asyncio
async def test_processing_to_shipped_does_not_fire_network_event():
    """Only DELIVERED/RETURNED transitions fire network events."""
    order = _build_order(status=OrderStatus.PROCESSING, payment_method="cod")
    network_repo = AsyncMock()
    network_repo.record_event = AsyncMock()
    network_repo.upsert_order = AsyncMock()

    use_case, store, _ = _build_use_case(order=order, network_repo=network_repo)
    await use_case.execute(
        order_id=order.id,
        dto=UpdateOrderStatusDTO(status="shipped"),
        store_id=order.store_id,
        user_id=store.owner_id,
    )

    network_repo.record_event.assert_not_called()
