"""`get_orders` read tool — order counts + revenue for a period (US1)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.repositories.order_repository import OrderRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "orders.view"
_STORE_TZ = ZoneInfo("Africa/Cairo")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {
            "type": "string",
            "enum": ["today", "7d", "30d"],
            "description": "Time window for the order metrics.",
        }
    },
    "additionalProperties": False,
}


def _range_for(period: str) -> tuple[datetime, datetime]:
    now = datetime.now(_STORE_TZ)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "30d":
        start = now - timedelta(days=30)
    else:  # default 7d
        start = now - timedelta(days=7)
    return start, now


async def get_orders(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission is not None and not await ctx.has_permission(
        REQUIRED_PERMISSION
    ):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    period = str(args.get("period") or "today")
    if period not in {"today", "7d", "30d"}:
        return ToolResult.invalid_args("period must be one of: today, 7d, 30d")

    start, end = _range_for(period)
    try:
        repo = OrderRepository(ctx.session)
        count = await repo.count_by_store(ctx.store_id, date_from=start, date_to=end)
        revenue_cents = await repo.get_revenue_by_date_range(ctx.store_id, start, end)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_tool_error", tool="get_orders", error=str(exc))
        return ToolResult.unavailable("Could not retrieve order metrics right now.")

    return ToolResult(
        ok=True,
        data={
            "period": period,
            "order_count": count,
            "revenue_minor_units": revenue_cents,
            "revenue": round(revenue_cents / 100, 2),
        },
    )


SPEC = {
    "name": "get_orders",
    "description": (
        "Get the store's order count and total revenue for a period (today, 7d, 30d). "
        "Use for questions like 'how many orders did I get today?'. Returns real numbers."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": get_orders,
}
