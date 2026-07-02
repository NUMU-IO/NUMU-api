"""`recommend_growth` read tool (US2) — grounded growth recommendations.

Detects the store's live signals (abandoned carts, catalog/bundles, repeat buyers)
and returns NUMU-feature recommendations grounded in authored growth playbooks, each
with a rationale, enablement how-to link, and a citable source. Only signals the
store actually exhibits are recommended (no generic advice) — Constitution II / SC-003.
"""

from __future__ import annotations

from typing import Any

from src.application.agent.knowledge.growth import recommend_growth
from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger

logger = get_logger(__name__)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


async def recommend_growth_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    try:
        recs = await recommend_growth(ctx.session, ctx.store_id, locale=ctx.locale)
    except Exception as exc:  # noqa: BLE001 — never fabricate advice
        logger.warning("agent_tool_error", tool="recommend_growth", error=str(exc))
        return ToolResult.unavailable(
            "Could not generate growth recommendations right now."
        )

    if not recs:
        # No signal → say there's nothing specific to recommend (not generic advice).
        return ToolResult(ok=True, data={"recommendations": []})

    return ToolResult(
        ok=True,
        data={"recommendations": recs},
        source=[
            {"type": "doc", "title": r["title"], "source": r["source"]} for r in recs
        ],
    )


SPEC = {
    "name": "recommend_growth",
    "description": (
        "Recommend NUMU features that would grow THIS store, based on its real signals "
        "(abandoned carts, product catalog, repeat buyers). Use for 'how can I grow my "
        "sales?'. Each recommendation is grounded in a growth playbook — cite it. If there "
        "are no relevant signals, say so rather than giving generic advice."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": None,  # returns aggregate signals + shared playbooks, no records
    "executor": recommend_growth_tool,
}
