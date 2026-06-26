"""`add_theme_section` write tool (US2) — builds a gated Action Proposal.

CONFIRM-tier: this tool VALIDATES and computes a before/after diff but applies
nothing. The agent loop surfaces the returned proposal; a separate confirm call
applies it via the existing theme-editor-v3 service (see application/agent/proposals.py).
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.config.logging_config import get_logger
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.tools._theme_common import (
    build_section_settings,
    build_v3_service,
    known_section_types,
    next_section_id,
    section_settings_schema,
)
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "themes.edit"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page": {"type": "string", "description": "Target page/template, e.g. 'home'."},
        "section_type": {
            "type": "string",
            "description": "A section type the active theme supports (see get_theme_config).",
        },
        "position": {
            "type": "integer",
            "description": "0-based insert index in the page's section order. Omit to append.",
        },
        "settings": {
            "type": "object",
            "description": "Optional settings for the new section; validated against its schema.",
        },
    },
    "required": ["page", "section_type"],
    "additionalProperties": False,
}


def _summary(locale: str, section_type: str, page: str) -> str:
    if locale == "ar":
        return f"إضافة قسم '{section_type}' في صفحة {page}"
    return f"Add a '{section_type}' section to the {page} page"


async def add_theme_section(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission is not None and not await ctx.has_permission(
        REQUIRED_PERMISSION
    ):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    page = args.get("page")
    section_type = args.get("section_type")
    if not page or not section_type:
        return ToolResult.invalid_args("Both 'page' and 'section_type' are required.")

    try:
        store_theme = await StoreThemeRepository(ctx.session).get_active_for_store(
            ctx.store_id
        )
        if store_theme is None:
            return ToolResult.unavailable("This store has no active theme.")
        available = known_section_types(store_theme.section_schemas)
        draft_res = await build_v3_service(ctx.session).get_draft_with_etag(
            ctx.store_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_tool_error", tool="add_theme_section", error=str(exc))
        return ToolResult.unavailable("Could not read the theme right now.")

    # Section type must be one the active theme provides (FR-007; US2 scenario 5).
    if section_type not in available:
        return ToolResult(
            ok=False,
            error_code="section_type_unavailable",
            error_message=(
                f"This theme doesn't offer a '{section_type}' section. "
                f"Available types: {', '.join(available) or 'none'}."
            ),
            data={"available_section_types": available},
        )

    draft = draft_res.get("draft") or {}
    templates = draft.get("templates") or {}
    if page not in templates:
        return ToolResult(
            ok=False,
            error_code="page_unavailable",
            error_message=f"No page '{page}'. Available pages: {', '.join(templates.keys()) or 'none'}.",
            data={"pages": list(templates.keys())},
        )

    # Validate/merge settings against the section schema (FR-011).
    schema_settings = (
        section_settings_schema(store_theme.section_schemas, section_type) or []
    )
    settings, err = build_section_settings(schema_settings, args.get("settings") or {})
    if err:
        return ToolResult.invalid_args(err)

    page_tpl = templates[page]
    sections = page_tpl.get("sections") or {}
    order_before = list(page_tpl.get("order") or [])
    new_id = next_section_id(sections, section_type)

    pos = args.get("position")
    if not isinstance(pos, int) or pos < 0 or pos > len(order_before):
        pos = len(order_before)
    order_after = order_before[:pos] + [new_id] + order_before[pos:]

    diff = {
        "page": page,
        "section_order_before": order_before,
        "section_order_after": order_after,
        "new_section": {"id": new_id, "type": section_type, "settings": settings},
    }
    summary = _summary(ctx.locale, section_type, page)

    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        proposal={
            "tool_name": "add_theme_section",
            "params": {
                "page": page,
                "section_type": section_type,
                "position": pos,
                "settings": settings,
                "new_section_id": new_id,
            },
            "diff": diff,
            "based_on_theme_version": draft_res.get("etag"),
            "summary": summary,
        },
    )


def locate_setting(draft: dict, setting_path: str) -> tuple[Any, str | None]:
    """Resolve a setting_path to its current value (US3).

    Path forms: `global.<key>` for a global setting, or `<page>.<section_id>.<key>`
    for a section setting. Returns (current_value_or_None, error).
    """
    parts = setting_path.split(".")
    if parts and parts[0] == "global":
        if len(parts) < 2:
            return None, "Global path must be 'global.<key>'."
        key = ".".join(parts[1:])
        return (draft.get("global_settings") or {}).get(key), None
    if len(parts) < 3:
        return None, "Path must be 'global.<key>' or '<page>.<section_id>.<key>'."
    page, section_id, key = parts[0], parts[1], ".".join(parts[2:])
    sections = ((draft.get("templates") or {}).get(page) or {}).get("sections") or {}
    if section_id not in sections:
        return None, f"No section '{section_id}' on page '{page}'."
    return (sections[section_id].get("settings") or {}).get(key), None


async def update_theme_setting(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission is not None and not await ctx.has_permission(
        REQUIRED_PERMISSION
    ):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    setting_path = args.get("setting_path")
    value = args.get("value")
    if not setting_path or value is None:
        return ToolResult.invalid_args("Both 'setting_path' and 'value' are required.")
    if not isinstance(value, str | int | float | bool):
        return ToolResult.invalid_args("'value' must be a simple text/number/boolean.")

    try:
        store_theme = await StoreThemeRepository(ctx.session).get_active_for_store(
            ctx.store_id
        )
        if store_theme is None:
            return ToolResult.unavailable("This store has no active theme.")
        draft_res = await build_v3_service(ctx.session).get_draft_with_etag(
            ctx.store_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_tool_error", tool="update_theme_setting", error=str(exc))
        return ToolResult.unavailable("Could not read the theme right now.")

    draft = draft_res.get("draft") or {}
    before, err = locate_setting(draft, setting_path)
    if err:
        return ToolResult.invalid_args(err)

    diff = {"setting_path": setting_path, "before": before, "after": value}
    summary = (
        f"تغيير '{setting_path}'" if ctx.locale == "ar" else f"Change '{setting_path}'"
    )
    return ToolResult(
        ok=True,
        data={"summary": summary, "diff": diff},
        proposal={
            "tool_name": "update_theme_setting",
            "params": {
                "setting_path": setting_path,
                "value": value,
                "locale": ctx.locale,
            },
            "diff": diff,
            "based_on_theme_version": draft_res.get("etag"),
            "summary": summary,
        },
    )


UPDATE_SETTING_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "setting_path": {
            "type": "string",
            "description": "Setting to change: 'global.<key>' or '<page>.<section_id>.<key>'.",
        },
        "value": {
            "description": "New value (text/number/boolean), validated against the setting.",
        },
        "locale": {"type": "string", "enum": ["ar", "en"]},
    },
    "required": ["setting_path", "value"],
    "additionalProperties": False,
}


SPEC = {
    "name": "add_theme_section",
    "description": (
        "Propose adding a section of a type the active theme supports to a page. Returns a "
        "preview/diff for the merchant to confirm — it does NOT apply the change. Call "
        "get_theme_config first to choose a valid section_type and page."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": add_theme_section,
}


UPDATE_SETTING_SPEC = {
    "name": "update_theme_setting",
    "description": (
        "Propose changing a single editable theme/section setting (e.g. a hero heading text). "
        "Returns a before/after preview for the merchant to confirm — it does NOT apply. Use "
        "get_theme_config to find the page/section, and 'global.<key>' for global settings."
    ),
    "input_schema": UPDATE_SETTING_INPUT_SCHEMA,
    "risk_tier": RiskTier.CONFIRM,
    "required_permission": REQUIRED_PERMISSION,
    "executor": update_theme_setting,
}
