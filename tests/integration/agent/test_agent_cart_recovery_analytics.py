"""Pillars 2+3 — abandoned-cart tools + analytics snapshot.

`get_abandoned_checkouts` must surface contactability as booleans only (never
addresses); `send_cart_recovery` must propose-not-send and fail closed on the
wrong store / recovered / no-email carts; `get_store_analytics` must compute
period-over-period trends; and the applier↔undoer symmetry guard must hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.application.agent.proposals import ACTION_APPLIERS, ACTION_UNDOERS
from src.application.agent.tool_registry import build_default_registry
from src.application.agent.tools import ToolContext
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.tools.abandoned_carts import get_abandoned_checkouts
from src.infrastructure.agent.tools.analytics import get_store_analytics
from src.infrastructure.agent.tools.cart_recovery import send_cart_recovery


@dataclass
class _Cart:
    id: object
    store_id: object
    email: str | None
    phone: str | None
    total: Decimal
    currency: str = "EGP"
    line_items: list = field(default_factory=list)
    abandoned_at: datetime | None = None
    recovered_at: datetime | None = None
    recovery_email_sent_at: datetime | None = None


def _ctx(*, allow: bool = True) -> ToolContext:
    async def has_permission(_code: str) -> bool:
        return allow

    return ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=None,
        locale="en",
        has_permission=has_permission,
    )


# ── get_abandoned_checkouts ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_carts_listed_with_booleans_never_addresses():
    ctx = _ctx()
    cart = _Cart(
        id=uuid4(),
        store_id=ctx.store_id,
        email="secret@example.com",
        phone=None,
        total=Decimal("250"),
        line_items=[{"product_name": "Hoodie", "quantity": 2}],
        abandoned_at=datetime.now(UTC),
    )
    repo = AsyncMock()
    repo.list_by_store.return_value = ([cart], 1)
    with patch(
        "src.infrastructure.agent.tools.abandoned_carts.AbandonedCheckoutRepository",
        return_value=repo,
    ):
        res = await get_abandoned_checkouts(ctx, {})
    assert res.ok
    item = res.data["checkouts"][0]
    assert item["has_email"] is True and item["has_phone"] is False
    # The model must never see the address itself.
    assert "secret@example.com" not in str(res.data)
    assert res.data["value_at_stake"] == 250.0


@pytest.mark.asyncio
async def test_carts_permission_gated():
    res = await get_abandoned_checkouts(_ctx(allow=False), {})
    assert not res.ok and res.error_code == "forbidden"


# ── send_cart_recovery ───────────────────────────────────────────────────────


def _recovery_patch(cart):
    repo = AsyncMock()
    repo.get_by_id.return_value = cart
    return patch(
        "src.infrastructure.agent.tools.cart_recovery.AbandonedCheckoutRepository",
        return_value=repo,
    )


@pytest.mark.asyncio
async def test_recovery_proposes_and_sends_nothing():
    ctx = _ctx()
    cart = _Cart(
        id=uuid4(),
        store_id=ctx.store_id,
        email="x@y.z",
        phone=None,
        total=Decimal("500"),
        line_items=[{"product_name": "Tee", "quantity": 1}],
    )
    with _recovery_patch(cart):
        res = await send_cart_recovery(ctx, {"checkout_id": str(cart.id)})
    assert res.ok
    assert res.proposal is not None
    assert res.proposal["tool_name"] == "send_cart_recovery"
    assert res.proposal["params"] == {"checkout_id": str(cart.id)}
    assert res.proposal["diff"]["already_emailed"] is False


@pytest.mark.asyncio
async def test_recovery_fails_closed():
    ctx = _ctx()
    wrong_store = _Cart(
        id=uuid4(), store_id=uuid4(), email="a@b.c", phone=None, total=Decimal("1")
    )
    with _recovery_patch(wrong_store):
        res = await send_cart_recovery(ctx, {"checkout_id": str(wrong_store.id)})
    assert not res.ok and res.error_code == "not_found"

    recovered = _Cart(
        id=uuid4(),
        store_id=ctx.store_id,
        email="a@b.c",
        phone=None,
        total=Decimal("1"),
        recovered_at=datetime.now(UTC),
    )
    with _recovery_patch(recovered):
        res = await send_cart_recovery(ctx, {"checkout_id": str(recovered.id)})
    assert not res.ok and res.error_code == "already_recovered"

    no_email = _Cart(
        id=uuid4(),
        store_id=ctx.store_id,
        email=None,
        phone="+20100",
        total=Decimal("1"),
    )
    with _recovery_patch(no_email):
        res = await send_cart_recovery(ctx, {"checkout_id": str(no_email.id)})
    assert not res.ok and res.error_code == "no_email"


# ── get_store_analytics ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analytics_trend_math():
    ctx = _ctx()
    repo = AsyncMock()
    # current period: 20 orders / EGP 400.00; previous: 10 orders / EGP 200.00
    repo.count_by_store.side_effect = [20, 10]
    repo.get_revenue_by_date_range.side_effect = [40000, 20000]
    with patch(
        "src.infrastructure.agent.tools.analytics.OrderRepository",
        return_value=repo,
    ):
        res = await get_store_analytics(ctx, {"period": "30d"})
    assert res.ok
    d = res.data
    assert d["current"] == {"orders": 20, "revenue": 400.0, "avg_order_value": 20.0}
    assert d["previous_period"]["revenue"] == 200.0
    assert d["trend"]["revenue_pct"] == 100.0
    assert d["trend"]["orders_pct"] == 100.0
    assert d["trend"]["avg_order_value_pct"] == 0.0


@pytest.mark.asyncio
async def test_analytics_no_baseline_and_validation():
    ctx = _ctx()
    repo = AsyncMock()
    repo.count_by_store.side_effect = [5, 0]
    repo.get_revenue_by_date_range.side_effect = [1000, 0]
    with patch(
        "src.infrastructure.agent.tools.analytics.OrderRepository",
        return_value=repo,
    ):
        res = await get_store_analytics(ctx, {"period": "7d"})
    assert res.ok
    assert res.data["trend"]["revenue_pct"] is None  # no previous-period baseline

    bad = await get_store_analytics(_ctx(), {"period": "1y"})
    assert not bad.ok and bad.error_code == "invalid_args"


# ── registration ─────────────────────────────────────────────────────────────


def test_registration_and_undoer_guard():
    r = build_default_registry()
    assert r.get("get_abandoned_checkouts").risk_tier == RiskTier.AUTO
    assert r.get("get_store_analytics").risk_tier == RiskTier.AUTO
    spec = r.get("send_cart_recovery")
    assert spec.risk_tier == RiskTier.CONFIRM
    assert spec.required_permission == "marketing.campaigns.edit"
    assert "send_cart_recovery" in ACTION_APPLIERS
    # Every applier must have an undoer (theme-path corruption guard).
    assert set(ACTION_APPLIERS) <= set(ACTION_UNDOERS)
