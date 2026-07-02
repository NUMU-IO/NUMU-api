"""`get_products` read tool — live catalog/inventory, incl. low-stock (US1)."""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.repositories.product_repository import ProductRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "products.view"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "low_stock_only": {
            "type": "boolean",
            "description": "If true, return only products at/below their low-stock threshold.",
        },
        "limit": {
            "type": "integer",
            "description": "Max products to return (1-50).",
            "minimum": 1,
            "maximum": 50,
        },
    },
    "additionalProperties": False,
}


def _product_to_dict(p) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "sku": p.sku,
        "price": float(p.price.amount),
        "currency": p.price.currency.value,
        "quantity": p.quantity,
        "low_stock_threshold": p.low_stock_threshold,
    }


async def get_products(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission is not None and not await ctx.has_permission(
        REQUIRED_PERMISSION
    ):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    limit = int(args.get("limit") or 20)
    limit = max(1, min(limit, 50))
    low_stock_only = bool(args.get("low_stock_only"))

    try:
        repo = ProductRepository(ctx.session)
        if low_stock_only:
            products = await repo.get_low_stock(ctx.store_id, limit=limit)
        else:
            products = await repo.get_by_store(ctx.store_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 — report as unavailable, never fabricate
        logger.warning("agent_tool_error", tool="get_products", error=str(exc))
        return ToolResult.unavailable("Could not retrieve products right now.")

    items = [_product_to_dict(p) for p in products]
    return ToolResult(
        ok=True,
        data={"count": len(items), "products": items},
        source=[{"type": "product", "id": i["id"]} for i in items],
    )


SPEC = {
    "name": "get_products",
    "description": (
        "List the store's products with live inventory. Use low_stock_only=true to "
        "answer 'which products are low on stock'. Returns real counts — never guess."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": get_products,
}
