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

# key → (path inside store.settings, human label, kind). Everything else is
# refused. `kind` is "text" or "bool"; a bool key accepts true/false only.
WRITABLE_SETTINGS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "favicon_url": (("customization", "identity", "favicon_url"), "Favicon", "text"),
    "store_name": (("customization", "identity", "store_name"), "Store name", "text"),
    # SEO / GEO / AEO. These live in the typed StoreSeoSettings blob the
    # storefront already reads for meta tags, JSON-LD and robots.txt — the
    # agent writes the same keys the SEO tab does, not a parallel set.
    "seo_title": (("seo", "seo_title"), "SEO title", "text"),
    "seo_description": (("seo", "seo_description"), "SEO description", "text"),
    "social_image_url": (("seo", "social_image_url"), "Social share image", "text"),
    "business_type": (("seo", "business_type"), "Business type", "text"),
    "google_site_verification": (
        ("seo", "google_site_verification"),
        "Google verification",
        "text",
    ),
    "bing_site_verification": (
        ("seo", "bing_site_verification"),
        "Bing verification",
        "text",
    ),
    "robots_indexing_enabled": (
        ("seo", "robots_indexing_enabled"),
        "Search engine indexing",
        "bool",
    ),
    "has_return_policy_30d": (
        ("seo", "has_return_policy_30d"),
        "30-day return policy",
        "bool",
    ),
    "arabic_content_ready": (
        ("seo", "arabic_content_ready"),
        "Arabic content ready",
        "bool",
    ),
    "short_answer": (("seo", "short_answer"), "Short answer", "text"),
    "ai_crawlers_allowed": (
        ("seo", "ai_crawlers_allowed"),
        "AI crawler access",
        "bool",
    ),
    "llms_txt_enabled": (("seo", "llms_txt_enabled"), "llms.txt", "bool"),
    "contact_email": (("seo", "contact_email"), "Contact email", "text"),
    "contact_phone": (("seo", "contact_phone"), "Contact phone", "text"),
    # JSON values: validated through StoreSeoSettings itself, so the agent can
    # never write a shape the storefront would later fail to read.
    "faqs": (("seo", "faqs"), "FAQs", "json"),
    "same_as": (("seo", "same_as"), "Official profiles", "json"),
    "area_served": (("seo", "area_served"), "Areas served", "json"),
}

# Length caps mirror StoreSeoSettings, so a value the agent writes can never be
# one the typed model would later reject.
_MAX_LEN = {"seo_title": 70, "seo_description": 160, "short_answer": 320}

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
                "New value. For favicon_url and social_image_url this must be "
                "an https URL. For the boolean keys pass 'true' or 'false' — "
                "robots_indexing_enabled false takes the store out of Google "
                "and puts Disallow: / in robots.txt."
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
    path, label, kind = entry

    raw = str(args.get("value") or "").strip()
    if not raw:
        return ToolResult.invalid_args("'value' cannot be empty.")

    if kind == "json":
        import json

        try:
            parsed_json = json.loads(raw)
        except ValueError:
            return ToolResult.invalid_args(f"'{key}' must be valid JSON.")
        # Round-trip through the real model: whatever it accepts is exactly
        # what the storefront can read back.
        from src.api.v1.schemas.tenant.store_seo import StoreSeoSettings

        try:
            validated = StoreSeoSettings.model_validate({key: parsed_json})
        except Exception as exc:  # noqa: BLE001 — surfaced to the model as-is
            return ToolResult.invalid_args(f"'{key}' is not valid: {exc}")
        value: Any = validated.model_dump()[key]
    elif kind == "bool":
        lowered = raw.lower()
        if lowered not in ("true", "false"):
            return ToolResult.invalid_args(f"'{key}' must be 'true' or 'false'.")
        value = lowered == "true"
    else:
        if key.endswith("_url") and not raw.startswith("https://"):
            # http:// is blocked as mixed content on the storefront anyway.
            return ToolResult.invalid_args("Image URLs must start with https://.")
        limit = _MAX_LEN.get(key)
        if limit and len(raw) > limit:
            return ToolResult.invalid_args(
                f"'{key}' must be at most {limit} characters (got {len(raw)})."
            )
        value = raw

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
