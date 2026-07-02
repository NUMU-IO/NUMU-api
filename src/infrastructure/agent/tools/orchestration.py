"""`trigger_workflow` tool (research R9) — allow-listed n8n dispatch.

Hands heavy / multi-step / async work to the existing self-hosted n8n via a
NAMED, allow-listed webhook. NUMU-api injects the tenant_id + server-side secret;
the LLM can only pick a workflow name, never a URL. v1 ships only the `ping`
example to prove the trigger→run→callback path end to end.
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.agent.n8n import N8nError, get_n8n_client

logger = get_logger(__name__)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "workflow": {
            "type": "string",
            "description": "An allow-listed workflow name (e.g. 'ping').",
        },
        "params": {
            "type": "object",
            "description": "Validated parameters for the workflow.",
        },
    },
    "required": ["workflow"],
    "additionalProperties": False,
}


async def trigger_workflow(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    workflow = args.get("workflow")
    params = args.get("params")
    if not isinstance(params, dict):
        params = {}

    client = get_n8n_client()
    if not workflow or not client.is_allowed(workflow):
        return ToolResult(
            ok=False,
            error_code="workflow_not_allowed",
            error_message="That workflow isn't available. Only allow-listed workflows can run.",
        )
    try:
        result = await client.trigger(workflow, tenant_id=ctx.tenant_id, params=params)
    except N8nError as exc:
        return ToolResult.unavailable(exc.message)
    return ToolResult(ok=True, data=result)


SPEC = {
    "name": "trigger_workflow",
    "description": (
        "Hand a heavy / async / bulk task to an allow-listed background workflow. The result "
        "arrives later via callback. v1 supports only 'ping' (a connectivity check)."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": None,
    "executor": trigger_workflow,
}
