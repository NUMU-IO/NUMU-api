"""A held card-gateway order is announced exactly when its payment lands."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.entities.order import OrderStatus
from src.core.events.order_events import OrderCreatedEvent, OrderStatusChangedEvent
from src.infrastructure.events.handlers.held_order_handler import (
    handle_held_order_paid,
)


def _event(previous: str, new: str) -> OrderStatusChangedEvent:
    return OrderStatusChangedEvent(
        order_id=uuid4(),
        order_number="ORD-1",
        store_id=uuid4(),
        store_name="s",
        customer_id=uuid4(),
        previous_status=previous,
        new_status=new,
    )


async def _run(event: OrderStatusChangedEvent) -> list:
    order = MagicMock(
        id=event.order_id,
        order_number="ORD-1",
        store_id=event.store_id,
        customer_id=event.customer_id,
        total=1000,
        currency="EGP",
    )
    repo = MagicMock(get_by_id=AsyncMock(return_value=order))
    bus = MagicMock()

    @asynccontextmanager
    async def session_local():
        yield MagicMock()

    with (
        patch(
            "src.infrastructure.database.connection.AsyncSessionLocal",
            session_local,
        ),
        patch(
            "src.infrastructure.repositories.order_repository.OrderRepository",
            return_value=repo,
        ),
        patch("src.infrastructure.events.setup.get_event_bus", return_value=bus),
    ):
        await handle_held_order_paid(event)
    return [c.args[0] for c in bus.publish.call_args_list]


@pytest.mark.asyncio
async def test_paid_held_order_publishes_order_created():
    event = _event(OrderStatus.AWAITING_PAYMENT.value, OrderStatus.PROCESSING.value)
    published = await _run(event)
    assert len(published) == 1
    assert isinstance(published[0], OrderCreatedEvent)
    assert published[0].order_id == event.order_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "new"),
    [
        (OrderStatus.PENDING.value, OrderStatus.PROCESSING.value),
        (OrderStatus.AWAITING_PAYMENT.value, OrderStatus.CANCELLED.value),
    ],
)
async def test_other_transitions_publish_nothing(previous, new):
    assert await _run(_event(previous, new)) == []


def test_held_order_can_be_paid_or_expired():
    from src.core.entities.order import VALID_STATUS_TRANSITIONS

    allowed = VALID_STATUS_TRANSITIONS[OrderStatus.AWAITING_PAYMENT]
    assert OrderStatus.PROCESSING in allowed
    assert OrderStatus.CANCELLED in allowed
