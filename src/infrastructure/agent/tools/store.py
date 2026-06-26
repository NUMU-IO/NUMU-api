"""`get_store_summary` read tool — a small grounding bundle for the store (US1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.application.agent.tools import ToolContext, ToolResult
from src.config.logging_config import get_logger
from src.core.agent.entities import RiskTier
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.product_repository import ProductRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "products.view"
_STORE_TZ = ZoneInfo("Africa/Cairo")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


async def get_store_summary(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission is not None and not await ctx.has_permission(
        REQUIRED_PERMISSION
    ):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    data: dict[str, Any] = {}
    try:
        product_repo = ProductRepository(ctx.session)
        data["active_products"] = await product_repo.count_active(ctx.store_id)
        data["low_stock_products"] = len(
            await product_repo.get_low_stock(ctx.store_id, limit=50)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_tool_error", tool="get_store_summary", error=str(exc))
        return ToolResult.unavailable("Could not retrieve the store summary right now.")

    # Orders are only included if the caller may view them (least privilege).
    if ctx.has_permission is None or await ctx.has_permission("orders.view"):
        try:
            now = datetime.now(_STORE_TZ)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            order_repo = OrderRepository(ctx.session)
            data["orders_today"] = await order_repo.count_by_store(
                ctx.store_id, date_from=start, date_to=now
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent_tool_error", tool="get_store_summary.orders", error=str(exc)
            )

    return ToolResult(ok=True, data=data)


SPEC = {
    "name": "get_store_summary",
    "description": (
        "A quick snapshot of the store: active product count, low-stock count, and "
        "today's order count. Use to anchor a general 'how is my store doing' answer."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": get_store_summary,
}
