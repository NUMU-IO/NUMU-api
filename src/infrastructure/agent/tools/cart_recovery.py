"""`send_cart_recovery` write tool (Pillar 2) — gated recovery email.

CONFIRM-tier: the executor verifies the checkout (this store, has an email, not
already recovered) and returns a preview proposal — it sends NOTHING. On
merchant confirmation, the applier sends the recovery email and stamps
`recovery_email_sent_at`, mirroring the dashboard endpoint. Contact details are
never surfaced to the model — only booleans/ids.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.repositories import AbandonedCheckoutRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "marketing.campaigns.edit"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "checkout_id": {
            "type": "string",
            "description": "The abandoned checkout's id — from get_abandoned_checkouts.",
        },
    },
    "required": ["checkout_id"],
    "additionalProperties": False,
}


def _summary(locale: str, total: float, currency: str, item_count: int) -> str:
    if locale == "ar":
        return f"إرسال إيميل استرجاع لعربة متروكة ({item_count} منتج، {total:g} {currency})"
    return f"Send recovery email for an abandoned cart ({item_count} items, {total:g} {currency})"


async def send_cart_recovery(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    try:
        checkout_id = UUID(str(args.get("checkout_id")))
    except (TypeError, ValueError):
        return ToolResult.invalid_args("'checkout_id' must be a valid id.")

    try:
        checkout = await AbandonedCheckoutRepository(ctx.session).get_by_id(checkout_id)
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the turn
        logger.warning("agent_tool_error", tool="send_cart_recovery", error=str(exc))
        return ToolResult.unavailable("Could not load that checkout right now.")

    # Fail closed on wrong tenant (repo filter) or wrong store.
    if checkout is None or checkout.store_id != ctx.store_id:
        return ToolResult(
            ok=False,
            error_code="not_found",
            error_message="Abandoned checkout not found in this store.",
        )
    if checkout.recovered_at is not None:
        return ToolResult(
            ok=False,
            error_code="already_recovered",
            error_message="This checkout was already recovered — no email needed.",
        )
    if not checkout.email:
        return ToolResult(
            ok=False,
            error_code="no_email",
            error_message="This checkout has no email address to send to.",
        )

    item_count = sum((li.get("quantity") or 0) for li in checkout.line_items)
    total = float(checkout.total)
    summary = _summary(ctx.locale, total, checkout.currency, item_count)
    diff = {
        "action": "send_recovery_email",
        "checkout_id": str(checkout.id),
        "cart_value": total,
        "currency": checkout.currency,
        "item_count": item_count,
        "already_emailed": checkout.recovery_email_sent_at is not None,
    }
    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        source=[{"type": "abandoned_checkout", "id": str(checkout.id)}],
        proposal={
            "tool_name": "send_cart_recovery",
            "params": {"checkout_id": str(checkout.id)},
            "diff": diff,
            "summary": summary,
        },
    )


SPEC = {
    "name": "send_cart_recovery",
    "description": (
        "Propose sending a recovery email to the shopper of ONE abandoned checkout. "
        "Returns a preview for the merchant to CONFIRM — nothing is sent until "
        "confirmed. Get checkout ids from get_abandoned_checkouts first. If the "
        "checkout was already emailed, say so and let the merchant decide."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": send_cart_recovery,
}
