"""Unit tests for the funnel emission service (server-side
``order_delivered`` step).

The helper is the single entry point for emitting order_delivered from
all four code paths that flip an order into DELIVERED status (manual
merchant action + Bosta/MyLerz/J&T webhooks). The contract we care
about most is *idempotency*: webhook replays and the manual+automated
race must not double-count a delivery in the funnel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.services.funnel_emit_service import (
    _DELIVERED_FLAG,
    emit_order_delivered,
)


def _make_order(*, metadata: dict | None = None, tenant_id=None):
    """Lightweight order-shaped object — we only touch the attributes
    the helper reads, so a plain object is enough."""

    class _O:
        pass

    o = _O()
    o.id = uuid4()
    o.order_number = "NUM-000123"
    o.store_id = uuid4()
    o.tenant_id = tenant_id if tenant_id is not None else uuid4()
    o.customer_id = uuid4()
    o.metadata = metadata if metadata is not None else {}
    o.total = 12_500
    o.currency = "EGP"
    o.created_at = datetime.now(UTC)
    return o


@pytest.mark.asyncio
async def test_emit_order_delivered_writes_event_and_flag():
    funnel_repo = AsyncMock()
    order_repo = AsyncMock()
    order = _make_order()

    await emit_order_delivered(order, funnel_repo, order_repo)

    funnel_repo.create.assert_awaited_once()
    kwargs = funnel_repo.create.await_args.kwargs
    assert kwargs["step"] == "order_delivered"
    assert kwargs["store_id"] == order.store_id
    assert kwargs["tenant_id"] == order.tenant_id
    assert kwargs["customer_id"] == order.customer_id
    assert kwargs["step_data"]["order_number"] == "NUM-000123"
    assert kwargs["step_data"]["total_cents"] == 12_500

    # Idempotency flag stamped on the order after the write.
    assert order.metadata[_DELIVERED_FLAG] is True
    order_repo.update.assert_awaited_once_with(order)


@pytest.mark.asyncio
async def test_emit_order_delivered_idempotent_replay():
    """If the flag is already set, no new funnel row is written."""
    funnel_repo = AsyncMock()
    order_repo = AsyncMock()
    order = _make_order(metadata={_DELIVERED_FLAG: True})

    await emit_order_delivered(order, funnel_repo, order_repo)

    funnel_repo.create.assert_not_awaited()
    order_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_order_delivered_skips_when_tenant_id_missing():
    """Defensive: a tenant-less order should never reach funnel_events
    (RLS would reject it on read anyway)."""
    funnel_repo = AsyncMock()
    order_repo = AsyncMock()
    order = _make_order(tenant_id=None)

    await emit_order_delivered(order, funnel_repo, order_repo)

    funnel_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_order_delivered_fails_open_on_repo_error():
    """If the funnel write blows up, we swallow it — the caller is the
    order state-machine and analytics must never block deliveries."""
    funnel_repo = AsyncMock()
    funnel_repo.create.side_effect = RuntimeError("db down")
    order_repo = AsyncMock()
    order = _make_order()

    # Should not raise.
    await emit_order_delivered(order, funnel_repo, order_repo)

    # Flag must NOT be set when the write failed — so the next call
    # gets a fresh chance to record the event.
    assert _DELIVERED_FLAG not in order.metadata
