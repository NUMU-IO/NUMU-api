"""Domain entities for the NUMU Agent.

These are framework-free dataclasses/enums (no SQLAlchemy, no Pydantic) so the
domain stays dependency-free per NUMU-api's Clean Architecture. Infrastructure
maps DB models <-> these entities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class TurnRole(StrEnum):
    USER = "user"
    AGENT = "agent"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class RiskTier(StrEnum):
    """Risk ladder (Constitution III / Security constraints).

    ``auto`` read tools execute immediately; ``confirm`` write tools return an
    ActionProposal and apply nothing until explicit confirmation; ``elevated``
    is reserved (pricing/destructive) and not shipped in v1.
    """

    AUTO = "auto"
    CONFIRM = "confirm"
    ELEVATED = "elevated"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    DECLINED = "declined"
    EXPIRED = "expired"


class AuditResult(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"


@dataclass
class Conversation:
    """A merchant's chat thread with the Agent, scoped to tenant + staff."""

    id: UUID
    tenant_id: UUID
    staff_id: UUID
    status: ConversationStatus = ConversationStatus.ACTIVE
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ToolCallRecord:
    """Sanitized record of one tool invocation within a turn (no secrets)."""

    name: str
    ok: bool
    # Short, sanitized metadata only — never raw credentials or cross-tenant ids.
    source: list[dict[str, str]] = field(default_factory=list)
    error_code: str | None = None


@dataclass
class Turn:
    """One merchant message + the Agent's response."""

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    role: TurnRole
    content: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    model_used: str | None = None
    latency_ms: int | None = None
    created_at: datetime | None = None


@dataclass
class ActionProposal:
    """A pending write the merchant has not yet confirmed (Constitution III).

    Carries the computed before/after diff and the theme version it was based on
    so a later confirm can enforce the stale-proposal guard (FR-015).
    """

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    tool_name: str
    params: dict
    diff: dict
    based_on_theme_version: str | None = None
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass
class AuditRecord:
    """Immutable record of an applied (or rejected-at-apply) write."""

    id: UUID
    tenant_id: UUID
    staff_id: UUID | None  # None for system actions (e.g. an n8n workflow callback)
    tool_name: str
    params: dict
    before_state: dict
    after_state: dict
    result: AuditResult
    conversation_id: UUID | None = None
    model_used: str | None = None
    created_at: datetime | None = None
