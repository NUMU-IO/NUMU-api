"""Phase 6 polish: n8n allow-list + HMAC callback, arg validation, reserved tools."""

from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

import pytest

from src.application.agent.tools import ToolSpec, validate_arguments
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.n8n.client import N8nClient, N8nError
from src.infrastructure.agent.tools.reserved import SPECS as RESERVED_SPECS


def _spec(schema) -> ToolSpec:
    async def _noop(ctx, args):
        return None

    return ToolSpec(
        name="t",
        description="d",
        input_schema=schema,
        risk_tier=RiskTier.AUTO,
        required_permission=None,
        executor=_noop,
    )


def test_validate_arguments_enforces_required_and_no_extras():
    schema = {
        "type": "object",
        "properties": {"page": {"type": "string"}, "section_type": {"type": "string"}},
        "required": ["page", "section_type"],
        "additionalProperties": False,
    }
    spec = _spec(schema)
    assert validate_arguments(spec, {"page": "home"}) is not None  # missing required
    assert (
        validate_arguments(spec, {"page": "home", "section_type": "x", "evil": 1})
        is not None
    )
    assert validate_arguments(spec, {"page": "home", "section_type": "x"}) is None
    assert validate_arguments(spec, "not-a-dict") is not None


def test_n8n_callback_hmac_verification():
    client = N8nClient(
        base_url="https://n8n.example", secret="topsecret", allowed_workflows=["ping"]
    )
    body = b'{"run_id":"r1","workflow":"ping","tenant_id":"t","status":"succeeded"}'
    good = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert client.verify_callback(body, good) is True
    assert client.verify_callback(body, "deadbeef") is False
    assert client.verify_callback(body, None) is False


@pytest.mark.asyncio
async def test_n8n_trigger_rejects_unlisted_workflow():
    client = N8nClient(
        base_url="https://n8n.example", secret="s", allowed_workflows=["ping"]
    )
    assert client.is_allowed("ping") is True
    assert client.is_allowed("delete_everything") is False
    with pytest.raises(N8nError):
        await client.trigger("delete_everything", tenant_id=uuid4(), params={})


@pytest.mark.asyncio
async def test_reserved_tools_decline():
    assert RESERVED_SPECS, "expected reserved tool specs"
    for spec in RESERVED_SPECS:
        result = await spec["executor"](None, {})
        assert result.ok is False
        assert result.error_code == "not_in_v1"
