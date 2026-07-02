"""`create_discount` write tool (Pillar 2) — gated coupon creation.

CONFIRM-tier: the executor validates + builds a proposal preview and creates
NOTHING. The merchant confirms via `/agent/confirm`, at which point
`apply_proposal` runs the coupon use-case (Constitution III — no write without
explicit confirmation). Tenant + permission are enforced here and re-checked in
the use-case; the LLM never supplies tenancy.
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger

logger = get_logger(__name__)

REQUIRED_PERMISSION = "coupon.create"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "The coupon code shoppers type at checkout, e.g. SUMMER10.",
        },
        "discount_type": {
            "type": "string",
            "enum": ["percentage", "fixed"],
            "description": "percentage = % off; fixed = flat amount off in store currency.",
        },
        "value": {
            "type": "number",
            "description": "Percentage 1-100, or the fixed amount off (> 0).",
        },
        "min_order_amount": {
            "type": "number",
            "description": "Optional minimum order subtotal required to use the coupon.",
        },
        "usage_limit": {
            "type": "integer",
            "minimum": 1,
            "description": "Optional cap on how many times the coupon can be used.",
        },
    },
    "required": ["code", "discount_type", "value"],
    "additionalProperties": False,
}


def _summary(locale: str, code: str, discount_type: str, value: float) -> str:
    amount = f"{value:g}%" if discount_type == "percentage" else f"{value:g}"
    if locale == "ar":
        kind = "نسبة" if discount_type == "percentage" else "مبلغ ثابت"
        return f"إنشاء كوبون خصم «{code}» ({kind} {amount})"
    return f"Create discount coupon “{code}” — {amount} off"


async def create_discount(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    code = (args.get("code") or "").strip().upper()
    discount_type = args.get("discount_type")
    if not code:
        return ToolResult.invalid_args("A coupon 'code' is required.")
    if discount_type not in ("percentage", "fixed"):
        return ToolResult.invalid_args("discount_type must be 'percentage' or 'fixed'.")
    try:
        value = float(args.get("value"))
    except (TypeError, ValueError):
        return ToolResult.invalid_args("'value' must be a number.")
    if value <= 0:
        return ToolResult.invalid_args("'value' must be greater than 0.")
    if discount_type == "percentage" and value > 100:
        return ToolResult.invalid_args("A percentage discount cannot exceed 100.")

    params: dict[str, Any] = {
        "code": code,
        "discount_type": discount_type,
        "value": value,
    }
    if args.get("min_order_amount") is not None:
        try:
            params["min_order_amount"] = float(args["min_order_amount"])
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'min_order_amount' must be a number.")
    if args.get("usage_limit") is not None:
        try:
            params["usage_limit"] = int(args["usage_limit"])
        except (TypeError, ValueError):
            return ToolResult.invalid_args("'usage_limit' must be an integer.")

    summary = _summary(ctx.locale, code, discount_type, value)
    diff = {"action": "create_coupon", **params}
    return ToolResult(
        ok=True,
        data={"summary": summary, "preview": params},
        proposal={
            "tool_name": "create_discount",
            "params": params,
            "diff": diff,
            "summary": summary,
        },
    )


SPEC = {
    "name": "create_discount",
    "description": (
        "Propose creating a discount coupon (percentage or fixed amount off). Returns a "
        "preview for the merchant to CONFIRM — it does NOT create anything until confirmed. "
        "Use for requests like 'make a 10% coupon called SUMMER10' or 'create 50 EGP off "
        "for orders over 500'."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": create_discount,
}
