"""Store-level write tools — storefront identity and open/closed state.

Both are CONFIRM-tier: they change what shoppers see, so the merchant reviews a
before/after card first and `apply_proposal` does the write.

Neither needed a schema change. The favicon already lives at
`settings.customization.identity.favicon_url`, and the storefront already
refuses to serve a store whose `status` is not ACTIVE — so closing one is
setting that status, not inventing a second notion of "closed" the storefront
would have to learn.

`update_store_settings` writes through a whitelist rather than accepting an
arbitrary path. A model that can set any key in a settings blob can set the
payment credentials in it too; the whitelist is what makes "the agent can edit
store settings" a bounded statement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger

logger = get_logger(__name__)

REQUIRED_PERMISSION = "settings.manage"

# key → (path inside store.settings, human label). Everything else is refused.
WRITABLE_SETTINGS: dict[str, tuple[tuple[str, ...], str]] = {
    "favicon_url": (("customization", "identity", "favicon_url"), "Favicon"),
    "store_name": (("customization", "identity", "store_name"), "Store name"),
}

UPDATE_SETTINGS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "enum": sorted(WRITABLE_SETTINGS),
            "description": "Which storefront setting to change.",
        },
        "value": {
            "type": "string",
            "description": (
                "New value. For favicon_url this must be an https URL — use "
                "the URL of an image the merchant just attached."
            ),
        },
    },
    "required": ["key", "value"],
    "additionalProperties": False,
}

SET_AVAILABILITY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "enum": ["open", "closed"],
            "description": "Whether shoppers can reach the storefront.",
        },
        "reopen_at": {
            "type": "string",
            "description": (
                "ISO-8601 datetime to reopen automatically. Only with "
                "state 'closed'. Omit to stay closed until reopened by hand."
            ),
        },
    },
    "required": ["state"],
    "additionalProperties": False,
}


def _dig(settings: dict, path: tuple[str, ...]):
    node = settings or {}
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


async def _load_store(ctx: ToolContext):
    from src.infrastructure.repositories.store_repository import StoreRepository

    return await StoreRepository(ctx.session).get_by_id(ctx.store_id)


async def update_store_settings(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    key = str(args.get("key") or "")
    entry = WRITABLE_SETTINGS.get(key)
    if entry is None:
        return ToolResult.invalid_args(
            f"'{key}' is not a setting this tool can change."
        )
    path, label = entry

    value = str(args.get("value") or "").strip()
    if not value:
        return ToolResult.invalid_args("'value' cannot be empty.")
    if key.endswith("_url") and not value.startswith("https://"):
        # http:// would be blocked as mixed content on the storefront anyway.
        return ToolResult.invalid_args("Image URLs must start with https://.")

    store = await _load_store(ctx)
    if store is None:
        return ToolResult.invalid_args("Store not found.")
    before = _dig(store.settings or {}, path)

    summary = (
        f"تغيير {label} للمتجر" if ctx.locale == "ar" else f"Change the store {label}"
    )
    diff = {
        "action": "update_store_settings",
        "key": key,
        "before": before,
        "after": value,
    }
    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        source=[{"type": "store", "id": str(ctx.store_id)}],
        proposal={
            "tool_name": "update_store_settings",
            "params": {"key": key, "value": value},
            "diff": diff,
            "summary": summary,
        },
    )


async def set_store_availability(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    state = str(args.get("state") or "").lower()
    if state not in ("open", "closed"):
        return ToolResult.invalid_args("'state' must be 'open' or 'closed'.")

    reopen_at = None
    raw = args.get("reopen_at")
    if raw:
        if state != "closed":
            return ToolResult.invalid_args("'reopen_at' only applies when closing.")
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return ToolResult.invalid_args("'reopen_at' must be an ISO-8601 datetime.")
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        if parsed <= datetime.now(UTC):
            return ToolResult.invalid_args("'reopen_at' must be in the future.")
        reopen_at = parsed.isoformat()

    store = await _load_store(ctx)
    if store is None:
        return ToolResult.invalid_args("Store not found.")
    current = getattr(store.status, "value", str(store.status))

    if state == "closed":
        when = f" until {reopen_at}" if reopen_at else ""
        summary = (
            f"إغلاق المتجر{' حتى ' + reopen_at if reopen_at else ''}"
            if ctx.locale == "ar"
            else f"Close the storefront{when}"
        )
    else:
        summary = "فتح المتجر" if ctx.locale == "ar" else "Reopen the storefront"

    diff = {
        "action": "set_store_availability",
        "before": current,
        "after": "active" if state == "open" else "inactive",
        "reopen_at": reopen_at,
    }
    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        source=[{"type": "store", "id": str(ctx.store_id)}],
        proposal={
            "tool_name": "set_store_availability",
            "params": {"state": state, "reopen_at": reopen_at},
            "diff": diff,
            "summary": summary,
        },
    )


UPDATE_SETTINGS_SPEC = {
    "name": "update_store_settings",
    "description": (
        "Propose changing a storefront setting the merchant can see: the "
        "favicon (the little icon in the browser tab) or the store name. "
        "Returns a before/after preview to CONFIRM — it changes nothing until "
        "confirmed. For a favicon, pass the https URL of an image the merchant "
        "attached. Use for 'change my favicon to this'."
    ),
    "input_schema": UPDATE_SETTINGS_INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": update_store_settings,
}

SET_AVAILABILITY_SPEC = {
    "name": "set_store_availability",
    "description": (
        "Propose closing the storefront to shoppers, or reopening it. Closing "
        "makes the shop unreachable — orders stop — so always confirm the "
        "merchant means it. Pass reopen_at to reopen automatically at a time "
        "(e.g. after a holiday); without it the store stays closed until "
        "reopened. Use for 'close my store until Saturday' or 'open the store'."
    ),
    "input_schema": SET_AVAILABILITY_INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": set_store_availability,
}
