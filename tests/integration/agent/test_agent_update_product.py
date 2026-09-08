"""Pillar 2 — `update_product` is a gated CONFIRM action with a real preview.

The executor must fetch the product to build a genuine before → after diff,
fail closed on missing permission or a product outside the caller's store,
and be registered as a CONFIRM tool wired to both an applier and an undoer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.application.agent.proposals import ACTION_APPLIERS, ACTION_UNDOERS
from src.application.agent.tool_registry import build_default_registry
from src.application.agent.tools import ToolContext
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.tools.update_product import update_product


@dataclass
class _Money:
    amount: Decimal


@dataclass
class _Product:
    id: object
    store_id: object
    name: str
    price: _Money
    compare_at_price: _Money | None
    quantity: int


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


def _repo_returning(product):
    repo = AsyncMock()
    repo.get_by_id.return_value = product
    return repo


@pytest.mark.asyncio
async def test_proposes_with_real_before_after_diff():
    ctx = _ctx()
    pid = uuid4()
    product = _Product(
        id=pid,
        store_id=ctx.store_id,
        name="Hoodie",
        price=_Money(Decimal("300")),
        compare_at_price=None,
        quantity=5,
    )
    with patch(
        "src.infrastructure.agent.tools.update_product.ProductRepository",
        return_value=_repo_returning(product),
    ):
        res = await update_product(
            ctx, {"product_id": str(pid), "price": 450, "quantity": 20}
        )
    assert res.ok
    assert res.proposal is not None
    diff = res.proposal["diff"]
    assert diff["before"] == {"price": 300.0, "quantity": 5}
    assert diff["after"] == {"price": 450.0, "quantity": 20}
    # Proposal params carry only the changes (+ product_id).
    assert res.proposal["params"] == {
        "product_id": str(pid),
        "price": 450.0,
        "quantity": 20,
    }


@pytest.mark.asyncio
async def test_rejects_product_from_another_store():
    ctx = _ctx()
    product = _Product(
        id=uuid4(),
        store_id=uuid4(),  # different store within (or beyond) the tenant
        name="Other",
        price=_Money(Decimal("100")),
        compare_at_price=None,
        quantity=1,
    )
    with patch(
        "src.infrastructure.agent.tools.update_product.ProductRepository",
        return_value=_repo_returning(product),
    ):
        res = await update_product(ctx, {"product_id": str(product.id), "price": 50})
    assert not res.ok
    assert res.error_code == "not_found"


@pytest.mark.asyncio
async def test_permission_gated_and_validation():
    denied = await update_product(
        _ctx(allow=False), {"product_id": str(uuid4()), "price": 10}
    )
    assert not denied.ok and denied.error_code == "forbidden"

    no_changes = await update_product(_ctx(), {"product_id": str(uuid4())})
    assert not no_changes.ok and no_changes.error_code == "invalid_args"

    bad_id = await update_product(_ctx(), {"product_id": "nope", "price": 10})
    assert not bad_id.ok and bad_id.error_code == "invalid_args"


@pytest.mark.asyncio
async def test_compare_at_must_exceed_price():
    ctx = _ctx()
    product = _Product(
        id=uuid4(),
        store_id=ctx.store_id,
        name="Tee",
        price=_Money(Decimal("200")),
        compare_at_price=None,
        quantity=3,
    )
    with patch(
        "src.infrastructure.agent.tools.update_product.ProductRepository",
        return_value=_repo_returning(product),
    ):
        res = await update_product(
            ctx, {"product_id": str(product.id), "compare_at_price": 150}
        )
    assert not res.ok and res.error_code == "invalid_args"


def test_registered_with_applier_and_undoer():
    spec = build_default_registry().get("update_product")
    assert spec is not None
    assert spec.risk_tier == RiskTier.CONFIRM
    assert spec.required_permission == "product.update"
    assert "update_product" in ACTION_APPLIERS
    # Guard: every action applier MUST have an undoer, or undo_last would push
    # its audit's before_state through the theme-restore path (theme corruption).
    assert set(ACTION_APPLIERS) <= set(ACTION_UNDOERS)
