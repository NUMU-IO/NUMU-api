"""Pillar 2 — `create_discount` is a gated CONFIRM action.

The executor must PROPOSE (never create), normalize + validate input, fail closed
without permission, and be registered as a CONFIRM tool wired to an applier.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.proposals import ACTION_APPLIERS
from src.application.agent.tool_registry import build_default_registry
from src.application.agent.tools import ToolContext
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.tools.create_discount import create_discount


def _ctx(*, allow: bool = True) -> ToolContext:
    async def has_permission(_code: str) -> bool:
        return allow

    return ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=None,  # executor never touches the session (propose only)
        locale="en",
        has_permission=has_permission,
    )


@pytest.mark.asyncio
async def test_proposes_and_creates_nothing():
    res = await create_discount(
        _ctx(), {"code": "summer10", "discount_type": "percentage", "value": 10}
    )
    assert res.ok
    assert res.proposal is not None
    assert res.proposal["tool_name"] == "create_discount"
    # Code is normalized to upper-case; nothing was created (no session use).
    assert res.proposal["params"]["code"] == "SUMMER10"
    assert res.proposal["params"]["value"] == 10


@pytest.mark.asyncio
async def test_permission_gated():
    res = await create_discount(
        _ctx(allow=False), {"code": "X", "discount_type": "fixed", "value": 50}
    )
    assert not res.ok
    assert res.error_code == "forbidden"


@pytest.mark.asyncio
async def test_validation_rejects_bad_values():
    over = await create_discount(
        _ctx(), {"code": "X", "discount_type": "percentage", "value": 200}
    )
    assert not over.ok and over.error_code == "invalid_args"

    empty = await create_discount(
        _ctx(), {"code": "  ", "discount_type": "fixed", "value": 5}
    )
    assert not empty.ok and empty.error_code == "invalid_args"


def test_registered_as_confirm_tool_with_applier():
    spec = build_default_registry().get("create_discount")
    assert spec is not None
    assert spec.risk_tier == RiskTier.CONFIRM
    assert spec.required_permission == "coupon.create"
    # The confirm path knows how to apply it.
    assert "create_discount" in ACTION_APPLIERS
