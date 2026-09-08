"""Daily digest — proactive 'since yesterday' greeting (Pillar 4).

Verifies the digest composes cleanly against an empty store (quiet), stays
resilient when a block query fails (never raises), and shapes each block with a
follow-up prompt the panel can offer as a one-tap action.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.application.agent.digest import build_daily_digest


@pytest.mark.asyncio
async def test_empty_store_is_quiet(test_session):
    digest = await build_daily_digest(test_session, store_id=uuid4())
    assert digest["quiet"] is True
    assert digest["blocks"] == []
    assert digest["window_hours"] == 24


@pytest.mark.asyncio
async def test_blocks_compose_with_prompts():
    """With signals present, each block carries a count and a follow-up prompt."""
    order_repo = AsyncMock()
    order_repo.count_by_store.return_value = 3
    order_repo.get_revenue_by_date_range.return_value = 145000  # minor units

    cart = type("C", (), {"total": 500.0})()
    cart_repo = AsyncMock()
    cart_repo.list_by_store.return_value = ([cart, cart], 2)

    prod = type("P", (), {"id": uuid4(), "name": "Hoodie", "quantity": 1})()
    prod_repo = AsyncMock()
    prod_repo.get_low_stock.return_value = [prod]

    with (
        patch("src.application.agent.digest.OrderRepository", return_value=order_repo),
        patch(
            "src.application.agent.digest.AbandonedCheckoutRepository",
            return_value=cart_repo,
        ),
        patch("src.application.agent.digest.ProductRepository", return_value=prod_repo),
    ):
        digest = await build_daily_digest(None, store_id=uuid4())

    assert digest["quiet"] is False
    by_kind = {b["kind"]: b for b in digest["blocks"]}
    assert by_kind["orders"]["count"] == 3
    assert by_kind["orders"]["revenue"] == 1450.0
    assert by_kind["abandoned_carts"]["count"] == 2
    assert by_kind["abandoned_carts"]["value_at_stake"] == 1000.0
    assert by_kind["low_stock"]["items"][0]["name"] == "Hoodie"
    assert all(b.get("prompt") for b in digest["blocks"])


@pytest.mark.asyncio
async def test_one_failing_block_does_not_blank_the_digest():
    order_repo = AsyncMock()
    order_repo.count_by_store.side_effect = RuntimeError("db down")
    cart_repo = AsyncMock()
    cart_repo.list_by_store.return_value = ([], 0)
    prod_repo = AsyncMock()
    prod_repo.get_low_stock.return_value = []

    with (
        patch("src.application.agent.digest.OrderRepository", return_value=order_repo),
        patch(
            "src.application.agent.digest.AbandonedCheckoutRepository",
            return_value=cart_repo,
        ),
        patch("src.application.agent.digest.ProductRepository", return_value=prod_repo),
    ):
        # Must not raise even though the orders block errors.
        digest = await build_daily_digest(None, store_id=uuid4())
    assert "generated_at" in digest
