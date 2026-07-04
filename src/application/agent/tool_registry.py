"""Build the Agent's tool registry.

US1 ships read-only tools (auto tier). US2/US3 add the gated write tools
(`add_theme_section`, `update_theme_setting`) and US4 adds `search_knowledge`;
those register here too as they land. Reserved/disabled tools (T035) will be
declared so the Agent declines them with "planned for a later release".
"""

from __future__ import annotations

from src.application.agent.tools import ToolRegistry, ToolSpec
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.tools import (
    create_discount,
    growth,
    knowledge,
    orchestration,
    orders,
    products,
    reserved,
    store,
    theme_read,
    theme_write,
    update_product,
)

# US1 read tools + US2/US3 theme read/write (CONFIRM-tier: propose only) + US4 RAG
# + the n8n orchestration lane + reserved (declared-disabled) tools.
_TOOL_SPECS = [
    products.SPEC,
    orders.SPEC,
    store.SPEC,
    theme_read.SPEC,
    theme_write.SPEC,
    theme_write.UPDATE_SETTING_SPEC,
    knowledge.SPEC,
    growth.SPEC,
    create_discount.SPEC,
    update_product.SPEC,
    orchestration.SPEC,
    *reserved.SPECS,
]


def _spec_from_dict(d: dict) -> ToolSpec:
    return ToolSpec(
        name=d["name"],
        description=d["description"],
        input_schema=d["input_schema"],
        risk_tier=d["risk_tier"]
        if isinstance(d["risk_tier"], RiskTier)
        else RiskTier(d["risk_tier"]),
        required_permission=d.get("required_permission"),
        executor=d["executor"],
    )


def build_default_registry() -> ToolRegistry:
    """Construct the registry with the currently-shipped tools (US1 = read-only)."""
    registry = ToolRegistry()
    for spec in _TOOL_SPECS:
        registry.register(_spec_from_dict(spec))
    return registry
