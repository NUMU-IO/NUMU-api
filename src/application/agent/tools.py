"""Tool abstraction for the NUMU Agent.

A Tool is the Agent's only capability surface (Constitution VIII). Every tool
declares a JSON-Schema input, a risk tier, and a required RBAC permission, and
its executor — NOT the LLM — re-checks tenant + permission and validates args
before doing anything, failing closed (Constitution I).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.agent.entities import RiskTier


@dataclass
class ToolContext:
    """Everything an executor needs to run safely within one authenticated turn.

    ``store_id`` is resolved from the tenant context by the route layer; the LLM
    never supplies tenancy. ``has_permission`` is injected by the caller so the
    executor can fail closed without importing the RBAC service directly.
    """

    tenant_id: UUID
    store_id: UUID
    staff_id: UUID
    session: AsyncSession
    locale: str = "en"
    has_permission: Callable[[str], Awaitable[bool]] | None = None


@dataclass
class ToolResult:
    """Uniform tool envelope returned to the model (sanitized, no secrets)."""

    ok: bool
    data: Any = None
    # Citation hints for the no-hallucination guarantee (e.g. order/product ids).
    source: list[dict[str, str]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    # Set by a CONFIRM-tier (write) tool: a pending Action Proposal the loop must
    # surface for explicit merchant confirmation instead of applying (Constitution III).
    # Shape: {tool_name, params, diff, based_on_theme_version, summary}.
    proposal: dict | None = None

    @classmethod
    def unavailable(cls, message: str) -> ToolResult:
        return cls(ok=False, error_code="unavailable", error_message=message)

    @classmethod
    def forbidden(cls, permission: str) -> ToolResult:
        return cls(
            ok=False,
            error_code="forbidden",
            error_message=f"Missing required permission: {permission}",
        )

    @classmethod
    def invalid_args(cls, message: str) -> ToolResult:
        return cls(ok=False, error_code="invalid_args", error_message=message)


ToolExecutor = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for arguments
    risk_tier: RiskTier
    required_permission: str | None
    executor: ToolExecutor

    def to_openai_tool(self) -> dict[str, Any]:
        """Render this tool for an OpenAI-compatible `tools` array."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


def validate_arguments(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    """Lightweight guard run before every executor (prompt-injection hardening).

    Tool arguments come from the (untrusted) model, which may have been nudged by
    injected text in store data or a message. We re-check shape here — required
    fields present, no unexpected keys when the schema forbids them — so a
    malformed/oversized call is rejected before it touches a service. Executors
    still do their own domain validation.
    """
    if not isinstance(args, dict):
        return "Tool arguments must be an object."
    schema = spec.input_schema or {}
    for req in schema.get("required", []) or []:
        if req not in args:
            return f"Missing required argument '{req}'."
    if schema.get("additionalProperties") is False:
        allowed = set((schema.get("properties") or {}).keys())
        extra = [k for k in args if k not in allowed]
        if extra:
            return f"Unexpected argument(s): {', '.join(extra)}."
    return None


class ToolRegistry:
    """Holds the tools available to the Agent for a given build."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        return [t.to_openai_tool() for t in self._tools.values()]
