"""`create_product` write tool (Pillar 2) — gated product creation.

CONFIRM-tier, and the mirror of `update_product`: the executor validates and
builds a preview, and creates nothing. The merchant confirms via
`/agent/confirm`, at which point `apply_proposal` runs the same
`CreateProductUseCase` that `POST /stores/{id}/products` runs
(Constitution III).

New products go **live on confirm**. The confirmation step is the review — the
merchant reads the price, the name and the images in the proposal card before
anything is written, so a second trip to the dashboard to flip a switch only
made the tool feel broken. `status: "draft"` is still available for a merchant
who says they want to finish it later.

The tool also fills the SEO a product needs to be findable — title, meta
description, tags and slug. The model supplies them when it has something
better to say; anything it leaves out is derived from the name and description
here, so a product is never created with empty SEO.
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
        "status": {
            "type": "string",
            "enum": ["active", "draft"],
            "description": (
                "'active' (default) publishes it on confirm. Use 'draft' only "
                "when the merchant says they want to finish it later."
            ),
        },
        "seo_title": {
            "type": "string",
            "description": (
                "Search-result title, at most 60 characters. Lead with the "
                "product name. Derived from the name when omitted."
            ),
        },
        "seo_description": {
            "type": "string",
            "description": (
                "Search-result summary, at most 160 characters — what the "
                "product is and why to buy it. Derived when omitted."
            ),
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Up to 10 short search keywords a shopper would actually type."
            ),
        },
    },
    "required": ["name", "price"],
    "additionalProperties": False,
}


_SEO_TITLE_MAX = 60
_SEO_DESC_MAX = 160
_MAX_TAGS = 10


def _clip(value: str, limit: int) -> str:
    """Trim to `limit`, on a word boundary where one is close to the end."""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    cut = value[:limit].rstrip()
    space = cut.rfind(" ")
    return cut[:space] if space > limit * 0.6 else cut


def _seo(args: dict[str, Any], name: str, description: str | None) -> dict[str, Any]:
    """SEO the model supplied, filled in from the product where it is missing.

    Empty SEO is the default a merchant never goes back to fix, so the fallback
    matters more than the ideal: a title and a description that name the product
    beat two empty columns.
    """
    title = str(args.get("seo_title") or "").strip() or name
    desc = str(args.get("seo_description") or "").strip() or (description or name)
    tags = [str(t).strip() for t in (args.get("tags") or []) if str(t).strip()][
        :_MAX_TAGS
    ]
    return {
        "seo_title": _clip(title, _SEO_TITLE_MAX),
        "seo_description": _clip(desc, _SEO_DESC_MAX),
        "tags": tags,
    }


def _summary(locale: str, name: str, price: float, currency: str, status: str) -> str:
    if locale == "ar":
        state = "مسودة" if status == "draft" else "منشور"
        return f"إضافة منتج «{name}» بسعر {price:g} {currency} ({state})"
    state = "draft" if status == "draft" else "published"
    return f"Add product “{name}” at {price:g} {currency} ({state})"


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

    description = (
        (str(args.get("description")).strip() or None)
        if args.get("description")
        else None
    )
    # Live on confirm unless the merchant asked to keep it a draft.
    status = "draft" if str(args.get("status") or "").lower() == "draft" else "active"

    product = {
        "name": name,
        "price": price,
        "compare_at_price": compare_at,
        "quantity": quantity,
        "description": description,
        "category_id": str(category_id) if category_id else None,
        "images": images,
        "status": status,
        "currency": currency,
        **_seo(args, name, description),
    }
    summary = _summary(ctx.locale, name, price, currency, status)
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
        "merchant to CONFIRM — it creates nothing until confirmed, and goes "
        "live on the storefront when they do. Fill seo_title, seo_description "
        "and tags so the product is findable; pass status 'draft' only if the "
        "merchant says they want to finish it later. Use for 'add a product "
        "called Linen Scarf for 450'. To change an existing product, use "
        "update_product instead."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": create_product,
}
