"""Editing and removing sections that already exist on a page (Phase H).

`add_theme_section` places a section and `update_theme_setting` changes one
value; between them a merchant could add and tweak, but never restyle a
section in one go or take one away. These two close that.

Both are CONFIRM-tier and write nothing: they read the draft, build a real
before → after, and hand it to the merchant. Authoring a *new* section type
stays out of chat — that is a React component, a schema, a build, an upload
and a merchant pressing Update, which is a fleet release rather than a
conversation.
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.agent.tools._theme_common import (
    build_v3_service,
    section_settings_schema,
)
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "themes.edit"

UPDATE_SECTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page": {
            "type": "string",
            "description": "Template key, e.g. 'home' — from get_theme_config.",
        },
        "section_id": {
            "type": "string",
            "description": "The section's id on that page, from get_theme_config.",
        },
        "settings": {
            "type": "object",
            "description": (
                "The setting keys to change and their new values. Only keys the "
                "section's schema declares are accepted."
            ),
        },
    },
    "required": ["page", "section_id", "settings"],
    "additionalProperties": False,
}

REMOVE_SECTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page": {"type": "string", "description": "Template key, e.g. 'home'."},
        "section_id": {"type": "string", "description": "The section to remove."},
    },
    "required": ["page", "section_id"],
    "additionalProperties": False,
}


async def _load_draft(ctx: ToolContext, tool: str):
    try:
        service = build_v3_service(ctx.session)
        return await service.get_draft_with_etag(ctx.store_id), None
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the turn
        logger.warning("agent_tool_error", tool=tool, error=str(exc))
        return None, ToolResult.unavailable("Could not read the theme right now.")


def _locate(draft: dict, page: str, section_id: str):
    """Return (template, section) or a ToolResult explaining what is missing."""
    templates = (draft or {}).get("templates") or {}
    tpl = templates.get(page)
    if tpl is None:
        return (
            None,
            None,
            ToolResult.invalid_args(
                f"Page '{page}' does not exist. Available: "
                f"{', '.join(sorted(templates)) or 'none'}."
            ),
        )
    sections = tpl.get("sections") or {}
    section = sections.get(section_id)
    if section is None:
        return (
            None,
            None,
            ToolResult.invalid_args(
                f"Section '{section_id}' is not on '{page}'. Available: "
                f"{', '.join(sorted(sections)) or 'none'}."
            ),
        )
    return tpl, section, None


async def update_section_settings(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    page = str(args.get("page") or "")
    section_id = str(args.get("section_id") or "")
    requested = args.get("settings")
    if not isinstance(requested, dict) or not requested:
        return ToolResult.invalid_args("'settings' must be a non-empty object.")

    loaded, err = await _load_draft(ctx, "update_section_settings")
    if err:
        return err
    draft = loaded.get("draft") or {}
    _, section, err = _locate(draft, page, section_id)
    if err:
        return err

    # Validate keys against the section type's own schema, so the agent can
    # never write something the theme will not render.
    #
    # Deliberately NOT build_section_settings: that fills in schema defaults
    # for every key it knows about, which is right when creating a section and
    # wrong here — it would silently reset the settings the merchant did not
    # mention back to their defaults.
    try:
        store_theme = await StoreThemeRepository(ctx.session).get_active_for_store(
            ctx.store_id
        )
        schemas = getattr(store_theme, "section_schemas", None)
    except Exception:  # noqa: BLE001 — validation is a nicety, the draft is truth
        schemas = None

    schema = section_settings_schema(schemas, section.get("type")) if schemas else None
    if schema:
        known = {s.get("id") for s in schema if isinstance(s, dict) and s.get("id")}
        unknown = [k for k in requested if known and k not in known]
        if unknown:
            return ToolResult.invalid_args(
                f"'{section.get('type')}' has no setting(s): {', '.join(sorted(unknown))}."
            )
    cleaned = dict(requested)

    current = section.get("settings") or {}
    before = {k: current.get(k) for k in cleaned}
    if before == cleaned:
        return ToolResult.invalid_args("Those settings already have those values.")

    summary = (
        f"تعديل إعدادات القسم «{section_id}» في {page}"
        if ctx.locale == "ar"
        else f"Update settings on “{section_id}” ({page})"
    )
    diff = {
        "action": "update_section_settings",
        "page": page,
        "section_id": section_id,
        "section_type": section.get("type"),
        "before": before,
        "after": cleaned,
    }
    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        proposal={
            "tool_name": "update_section_settings",
            "params": {"page": page, "section_id": section_id, "settings": cleaned},
            "diff": diff,
            "based_on_theme_version": loaded.get("etag"),
            "summary": summary,
        },
    )


async def remove_section(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    page = str(args.get("page") or "")
    section_id = str(args.get("section_id") or "")

    loaded, err = await _load_draft(ctx, "remove_section")
    if err:
        return err
    draft = loaded.get("draft") or {}
    tpl, section, err = _locate(draft, page, section_id)
    if err:
        return err

    order_before = list(tpl.get("order") or [])
    order_after = [s for s in order_before if s != section_id]

    summary = (
        f"حذف القسم «{section_id}» من {page}"
        if ctx.locale == "ar"
        else f"Remove “{section_id}” from {page}"
    )
    diff = {
        "action": "remove_section",
        "page": page,
        "section_id": section_id,
        "section_type": section.get("type"),
        "section_order_before": order_before,
        "section_order_after": order_after,
    }
    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        proposal={
            "tool_name": "remove_section",
            "params": {
                "page": page,
                "section_id": section_id,
                # The whole block travels with the proposal so undo can put it
                # back exactly as it was, position included, without needing a
                # snapshot of the entire theme.
                "removed_section": section,
                "position": order_before.index(section_id)
                if section_id in order_before
                else len(order_before),
            },
            "diff": diff,
            "based_on_theme_version": loaded.get("etag"),
            "summary": summary,
        },
    )


UPDATE_SECTION_SPEC = {
    "name": "update_section_settings",
    "description": (
        "Propose changing several settings on a section that is already on a "
        "page — e.g. the heading and subheading of the hero at once. Returns a "
        "before/after preview for the merchant to CONFIRM; it changes nothing. "
        "Use get_theme_config first for the page and section_id. For a single "
        "value, update_theme_setting is equally fine."
    ),
    "input_schema": UPDATE_SECTION_INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": update_section_settings,
}

REMOVE_SECTION_SPEC = {
    "name": "remove_section",
    "description": (
        "Propose removing a section from a page. Returns the page's section "
        "order before and after for the merchant to CONFIRM; it removes "
        "nothing until confirmed, and the removal can be undone."
    ),
    "input_schema": REMOVE_SECTION_INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": remove_section,
}
