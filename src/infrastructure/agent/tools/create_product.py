"""`create_product` write tool (Pillar 2) — gated product creation.

CONFIRM-tier, and the mirror of `update_product`: the executor validates and
builds a preview, and creates nothing. The merchant confirms via
`/agent/confirm`, at which point `apply_proposal` runs the same
`CreateProductUseCase` that `POST /stores/{id}/products` runs
(Constitution III).

New products land as **drafts**. A model that misheard a price should not be
able to put a live, buyable product in front of shoppers on one confirmation;
publishing stays the merchant's separate, deliberate act in the dashboard.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger

logger = get_logger(__name__)

REQUIRED_PERMISSION = "product.create"

_MAX_IMAGES = 8

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Product name as the shopper will see it.",
        },
        "price": {
            "type": "number",
            "description": "Selling price (> 0) in the store currency, major units.",
        },
        "compare_at_price": {
            "type": "number",
            "description": "Optional strikethrough price; must exceed price.",
        },
        "quantity": {
            "type": "integer",
            "minimum": 0,
            "description": "Opening stock quantity. Defaults to 0.",
        },
        "description": {
            "type": "string",
            "description": "Optional product description.",
        },
        "category_id": {
            "type": "string",
            "description": "Optional category id; must belong to this store.",
        },
        "images": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional image URLs, https only — e.g. one the merchant just "
                "attached to the conversation."
            ),
        },
    },
    "required": ["name", "price"],
    "additionalProperties": False,
}


def _summary(locale: str, name: str, price: float, currency: str) -> str:
    if locale == "ar":
        return f"إضافة منتج «{name}» بسعر {price:g} {currency} (مسودة)"
    return f"Add product “{name}” at {price:g} {currency} (draft)"


async def create_product(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    name = str(args.get("name") or "").strip()
    if not name:
        return ToolResult.invalid_args("'name' is required.")

    try:
        price = float(args["price"])
    except (KeyError, TypeError, ValueError):
        return ToolResult.invalid_args("'price' must be a number.")
    if price <= 0:
        return ToolResult.invalid_args("'price' must be greater than 0.")

    compare_at = None
    if args.get("compare_at_price") is not None:
        try:
            compare_at = float(args["compare_at_price"])
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'compare_at_price' must be a number.")
        if compare_at <= price:
            return ToolResult.invalid_args(
                "compare_at_price must be greater than the selling price."
            )

    quantity = 0
    if args.get("quantity") is not None:
        try:
            quantity = int(args["quantity"])
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'quantity' must be an integer.")
        if quantity < 0:
            return ToolResult.invalid_args("'quantity' cannot be negative.")

    images = args.get("images") or []
    if not isinstance(images, list):
        return ToolResult.invalid_args("'images' must be a list of URLs.")
    images = [str(u) for u in images][:_MAX_IMAGES]
    # https only: an http URL would be a mixed-content image on the storefront,
    # and anything else is not a URL we should be storing on a product.
    if any(not u.startswith("https://") for u in images):
        return ToolResult.invalid_args("Image URLs must start with https://.")

    category_id = None
    if args.get("category_id"):
        try:
            category_id = UUID(str(args["category_id"]))
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'category_id' must be a valid id.")

    # The category is verified here rather than left to the use case so the
    # merchant sees the problem in the preview, not after confirming.
    if category_id is not None:
        try:
            from src.infrastructure.repositories.category_repository import (
                CategoryRepository,
            )

            category = await CategoryRepository(ctx.session).get_by_id(category_id)
        except Exception as exc:  # noqa: BLE001 — degrade, never 500 the turn
            logger.warning("agent_tool_error", tool="create_product", error=str(exc))
            return ToolResult.unavailable("Could not check that category right now.")
        if category is None or category.store_id != ctx.store_id:
            return ToolResult(
                ok=False,
                error_code="not_found",
                error_message="Category not found in this store.",
            )

    currency = "EGP"
    try:
        from src.infrastructure.repositories.store_repository import StoreRepository

        store = await StoreRepository(ctx.session).get_by_id(ctx.store_id)
        if store is not None and store.default_currency:
            currency = getattr(
                store.default_currency, "value", str(store.default_currency)
            )
    except Exception:  # noqa: BLE001 — the preview is still useful without it
        logger.warning("agent_tool_currency_lookup_failed", tool="create_product")

    product = {
        "name": name,
        "price": price,
        "compare_at_price": compare_at,
        "quantity": quantity,
        "description": (str(args.get("description")).strip() or None)
        if args.get("description")
        else None,
        "category_id": str(category_id) if category_id else None,
        "images": images,
        "status": "draft",
        "currency": currency,
    }
    summary = _summary(ctx.locale, name, price, currency)
    diff = {"action": "create_product", "product": product}

    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        source=[{"type": "store", "id": str(ctx.store_id)}],
        proposal={
            "tool_name": "create_product",
            "params": product,
            "diff": diff,
            "summary": summary,
        },
    )


SPEC = {
    "name": "create_product",
    "description": (
        "Propose adding a NEW product to the store. Returns a preview for the "
        "merchant to CONFIRM — it creates nothing until confirmed. The product is "
        "created as a DRAFT, so it is not visible to shoppers until the merchant "
        "publishes it. Use for 'add a product called Linen Scarf for 450'. To "
        "change an existing product, use update_product instead."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": create_product,
}
