"""`get_abandoned_checkouts` read tool (Pillar 3) — recoverable carts.

AUTO-tier read: lists the store's abandoned (not-yet-recovered) checkouts with
ids, totals, and contact availability so the model can ground answers like
"how much revenue is sitting in abandoned carts?" and chain into the gated
`send_cart_recovery` action. Emails/phones are surfaced ONLY as booleans — the
model needs to know a cart is contactable, never the address itself.
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.repositories import AbandonedCheckoutRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "orders.view"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Max carts to return (default 10, newest activity first).",
        },
        "only_contactable": {
            "type": "boolean",
            "description": "Only carts with an email or phone (default true — "
            "those are the ones a recovery message can reach).",
        },
    },
    "additionalProperties": False,
}


async def get_abandoned_checkouts(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    limit = min(int(args.get("limit") or 10), 20)
    only_contactable = args.get("only_contactable")
    has_contact = True if only_contactable in (None, True) else None

    try:
        repo = AbandonedCheckoutRepository(ctx.session)
        carts, total = await repo.list_by_store(
            ctx.store_id,
            limit=limit,
            abandoned_only=True,
            recovered_only=False,
            has_contact=has_contact,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the turn
        logger.warning(
            "agent_tool_error", tool="get_abandoned_checkouts", error=str(exc)
        )
        return ToolResult.unavailable("Could not load abandoned checkouts right now.")

    items = [
        {
            "id": str(c.id),
            "total": float(c.total),
            "currency": c.currency,
            "item_count": sum((li.get("quantity") or 0) for li in c.line_items),
            "items": [(li.get("product_name") or "Item") for li in c.line_items[:5]],
            "has_email": bool(c.email),
            "has_phone": bool(c.phone),
            "abandoned_at": c.abandoned_at.isoformat() if c.abandoned_at else None,
            "recovery_email_sent_at": (
                c.recovery_email_sent_at.isoformat()
                if c.recovery_email_sent_at
                else None
            ),
        }
        for c in carts
    ]
    return ToolResult(
        ok=True,
        data={
            "count": len(items),
            "total_matching": total,
            "value_at_stake": round(sum(i["total"] for i in items), 2),
            "checkouts": items,
        },
        source=[{"type": "abandoned_checkout", "id": i["id"]} for i in items],
    )


SPEC = {
    "name": "get_abandoned_checkouts",
    "description": (
        "List the store's abandoned (not recovered) checkouts — id, cart value, items, "
        "whether the shopper left an email/phone, and whether a recovery email was "
        "already sent. Use for 'how many abandoned carts do I have?' or before proposing "
        "send_cart_recovery."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": get_abandoned_checkouts,
}
