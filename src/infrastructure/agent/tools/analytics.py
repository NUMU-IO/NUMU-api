"""`get_store_analytics` read tool (Pillar 3) — revenue trend snapshot.

AUTO-tier read: revenue, order count, and average order value for a period,
compared against the immediately-preceding period of equal length (trend %).
Grounds "how is my store doing this month?" in real numbers the model can cite.
Built on OrderRepository aggregates only — no heavyweight analytics rollups.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.repositories.order_repository import OrderRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "analytics.view"

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {
            "type": "string",
            "enum": ["7d", "30d", "90d"],
            "description": "Window to analyze (default 30d), compared to the "
            "previous window of the same length.",
        },
    },
    "additionalProperties": False,
}


def _pct_change(current: float, previous: float) -> float | None:
    """Percent change vs the previous period; None when there's no baseline."""
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1)


async def get_store_analytics(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    period = str(args.get("period") or "30d")
    days = _PERIOD_DAYS.get(period)
    if days is None:
        return ToolResult.invalid_args("period must be one of: 7d, 30d, 90d")

    now = datetime.now(UTC)
    cur_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=days * 2)

    try:
        repo = OrderRepository(ctx.session)
        cur_count = await repo.count_by_store(
            ctx.store_id, date_from=cur_start, date_to=now
        )
        cur_revenue_minor = await repo.get_revenue_by_date_range(
            ctx.store_id, cur_start, now
        )
        prev_count = await repo.count_by_store(
            ctx.store_id, date_from=prev_start, date_to=cur_start
        )
        prev_revenue_minor = await repo.get_revenue_by_date_range(
            ctx.store_id, prev_start, cur_start
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the turn
        logger.warning("agent_tool_error", tool="get_store_analytics", error=str(exc))
        return ToolResult.unavailable("Could not retrieve store analytics right now.")

    cur_revenue = round(cur_revenue_minor / 100, 2)
    prev_revenue = round(prev_revenue_minor / 100, 2)
    cur_aov = round(cur_revenue / cur_count, 2) if cur_count else 0.0
    prev_aov = round(prev_revenue / prev_count, 2) if prev_count else 0.0

    return ToolResult(
        ok=True,
        data={
            "period": period,
            "current": {
                "orders": cur_count,
                "revenue": cur_revenue,
                "avg_order_value": cur_aov,
            },
            "previous_period": {
                "orders": prev_count,
                "revenue": prev_revenue,
                "avg_order_value": prev_aov,
            },
            "trend": {
                "orders_pct": _pct_change(cur_count, prev_count),
                "revenue_pct": _pct_change(cur_revenue, prev_revenue),
                "avg_order_value_pct": _pct_change(cur_aov, prev_aov),
            },
        },
    )


SPEC = {
    "name": "get_store_analytics",
    "description": (
        "Get the store's performance snapshot for a period (7d/30d/90d): revenue, "
        "order count, and average order value, each compared to the previous period "
        "(trend %). Use for 'how is my store doing?', 'did sales grow this month?', "
        "or before recommending growth actions. Cite the numbers it returns."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": get_store_analytics,
}
