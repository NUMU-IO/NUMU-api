"""Reserved, declared-but-disabled tools (T035).

Declared so the Agent recognizes the intent and declines with "planned for a
later release" rather than improvising. These are `elevated`-tier and always
return a not-available result in v1 (see contracts/tools.md > Reserved).
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier

_RESERVED: list[tuple[str, str]] = [
    ("generate_custom_section", "Generate a brand-new custom-coded theme section"),
    # create_discount is now a real gated action tool (Pillar 2).
    ("update_product", "Bulk-update product fields"),
]

_OPEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


def _make(name: str, desc: str) -> dict:
    async def _executor(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=False,
            error_code="not_in_v1",
            error_message=f"'{name}' isn't available yet — it's planned for a later release.",
        )

    return {
        "name": name,
        "description": f"(Reserved — NOT available in v1) {desc}. Calling it returns a polite decline.",
        "input_schema": _OPEN_SCHEMA,
        "risk_tier": RiskTier.ELEVATED,
        "required_permission": None,
        "executor": _executor,
    }


SPECS = [_make(name, desc) for name, desc in _RESERVED]
