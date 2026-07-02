"""`get_theme_config` read tool — active V3 theme types + page section order (US2)."""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger
from src.infrastructure.agent.tools._theme_common import (
    build_v3_service,
    known_section_types,
)
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository

logger = get_logger(__name__)

REQUIRED_PERMISSION = "themes.view"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page": {
            "type": "string",
            "description": "Optional page/template to focus (e.g. 'home'). Omit for all pages.",
        }
    },
    "additionalProperties": False,
}


async def get_theme_config(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission is not None and not await ctx.has_permission(
        REQUIRED_PERMISSION
    ):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

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
        logger.warning("agent_tool_error", tool="get_theme_config", error=str(exc))
        return ToolResult.unavailable(
            "Could not read the theme configuration right now."
        )

    draft = draft_res.get("draft") or {}
    templates = draft.get("templates") or {}
    requested = args.get("page")

    pages = {
        name: {"section_order": tpl.get("order", [])}
        for name, tpl in templates.items()
        if (not requested or name == requested)
    }

    return ToolResult(
        ok=True,
        data={
            "theme_slug": store_theme.theme_slug,
            "theme_name": store_theme.theme_name,
            "version": draft_res.get("etag"),
            "available_section_types": available,
            "pages": pages,
        },
    )


SPEC = {
    "name": "get_theme_config",
    "description": (
        "Read the active theme: which section TYPES it supports and the current section "
        "order per page. Call this before proposing to add a section, to pick a real type."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": get_theme_config,
}
