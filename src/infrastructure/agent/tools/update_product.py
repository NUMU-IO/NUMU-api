"""`update_product` write tool (Pillar 2) — gated price/stock update.

CONFIRM-tier: the executor fetches the product (tenant-scoped), builds a real
before → after preview, and changes NOTHING. The merchant confirms via
`/agent/confirm`, at which point `apply_proposal` runs UpdateProductUseCase
(Constitution III). The model supplies `product_id` (from a prior get_products
call); the executor re-verifies it belongs to the caller's store, so a
confused/injected id can never preview or touch another store's product.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.repositories.product_repository import ProductRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "product.update"

# v1 scope: the pricing/inventory essentials, plus the name. Anything else
# stays in the dashboard.
#
# `name` was missing, and the model's response to "rename to test5" was not to
# say so — it re-proposed the PREVIOUS change (quantity 500 → 500) and asked
# the merchant to confirm a no-op. A tool that cannot do a thing has to be
# able to say which thing, so the model can answer instead of improvising.
_EDITABLE_FIELDS = ("name", "price", "compare_at_price", "quantity")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_id": {
            "type": "string",
            "description": "The product's id — get it from get_products first.",
        },
        "name": {
            "type": "string",
            "minLength": 2,
            "maxLength": 200,
            "description": (
                "New product name. The URL slug is deliberately left alone, so "
                "existing links and search rankings survive a rename."
            ),
        },
        "price": {
            "type": "number",
            "description": "New selling price (> 0), in the store currency.",
        },
        "compare_at_price": {
            "type": "number",
            "description": "New compare-at (strikethrough) price; must exceed price.",
        },
        "quantity": {
            "type": "integer",
            "minimum": 0,
            "description": "New stock quantity on hand.",
        },
    },
    "required": ["product_id"],
    "additionalProperties": False,
}


def _summary(locale: str, name: str, changes: dict[str, Any]) -> str:
    fields = ", ".join(changes)
    if locale == "ar":
        return f"تعديل المنتج «{name}» ({fields})"
    return f"Update product “{name}” — {fields}"


async def update_product(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    try:
        product_id = UUID(str(args.get("product_id")))
    except (TypeError, ValueError):
        return ToolResult.invalid_args("'product_id' must be a valid product id.")

    changes: dict[str, Any] = {}
    if args.get("name") is not None:
        name = str(args["name"]).strip()
        if len(name) < 2:
            return ToolResult.invalid_args("'name' must be at least 2 characters.")
        if len(name) > 200:
            return ToolResult.invalid_args("'name' cannot exceed 200 characters.")
        changes["name"] = name
    if args.get("price") is not None:
        try:
            price = float(args["price"])
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'price' must be a number.")
        if price <= 0:
            return ToolResult.invalid_args("'price' must be greater than 0.")
        changes["price"] = price
    if args.get("compare_at_price") is not None:
        try:
            cap = float(args["compare_at_price"])
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'compare_at_price' must be a number.")
        if cap <= 0:
            return ToolResult.invalid_args("'compare_at_price' must be greater than 0.")
        changes["compare_at_price"] = cap
    if args.get("quantity") is not None:
        try:
            quantity = int(args["quantity"])
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'quantity' must be an integer.")
        if quantity < 0:
            return ToolResult.invalid_args("'quantity' cannot be negative.")
        changes["quantity"] = quantity

    if not changes:
        return ToolResult.invalid_args(
            f"Provide at least one of: {', '.join(_EDITABLE_FIELDS)}."
        )

    try:
        product = await ProductRepository(ctx.session).get_by_id(product_id)
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the turn
        logger.warning("agent_tool_error", tool="update_product", error=str(exc))
        return ToolResult.unavailable("Could not load that product right now.")

    # Fail closed on wrong tenant (repo filter) or wrong store (multi-store tenant).
    if product is None or product.store_id != ctx.store_id:
        return ToolResult(
            ok=False,
            error_code="not_found",
            error_message="Product not found in this store.",
        )

    before = {
        "name": product.name,
        "price": float(product.price.amount),
        "compare_at_price": (
            float(product.compare_at_price.amount) if product.compare_at_price else None
        ),
        "quantity": product.quantity,
    }
    after = {**before, **changes}

    # Drop anything already at the requested value. The model re-sends the
    # previous turn's arguments when it cannot do what was actually asked, and
    # a card reading "stock 500 → 500" asks the merchant to confirm nothing —
    # it looks like the assistant misunderstood, and confirming it teaches them
    # the previews cannot be trusted.
    changes = {k: v for k, v in changes.items() if before[k] != v}
    if not changes:
        return ToolResult.invalid_args(
            "Every value given already matches the product; nothing would change. "
            "Tell the merchant it is already set, or ask which field to change."
        )
    if (
        after.get("compare_at_price") is not None
        and after["compare_at_price"] <= after["price"]
    ):
        return ToolResult.invalid_args(
            "compare_at_price must be greater than the selling price."
        )

    summary = _summary(ctx.locale, product.name, changes)
    diff = {
        "action": "update_product",
        "product": {"id": str(product.id), "name": product.name},
        "before": {k: before[k] for k in changes},
        "after": {k: after[k] for k in changes},
    }
    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        source=[{"type": "product", "id": str(product.id)}],
        proposal={
            "tool_name": "update_product",
            "params": {"product_id": str(product.id), **changes},
            "diff": diff,
            "summary": summary,
        },
    )


SPEC = {
    "name": "update_product",
    "description": (
        "Propose renaming a product or updating its price, compare-at price, "
        "or stock quantity. "
        "Returns a before/after preview for the merchant to CONFIRM — it does NOT "
        "change anything until confirmed. Call get_products first to find the "
        "product_id. Use for 'rename it to Winter Hoodie', 'change the hoodie "
        "price to 450' or 'set stock to 20'."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": update_product,
}
