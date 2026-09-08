"""SQLAlchemy models for the NUMU Agent.

All tables are tenant-scoped (TenantMixin → `tenant_id` + RLS) and live in the
`public` schema like the rest of NUMU-api's tenant tables. Theme state is NOT
duplicated here — writes land in the existing `store_themes` via theme-editor-v3.
"""

from __future__ import annotations

from uuid import UUID as PyUUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class AgentConversationModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A merchant's chat thread with the Agent."""

    __tablename__ = "agent_conversations"
    __table_args__ = {"schema": "public"}

    staff_id: Mapped[PyUUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class AgentTurnModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """One merchant message + the Agent's response."""

    __tablename__ = "agent_turns"
    __table_args__ = {"schema": "public"}

    conversation_id: Mapped[PyUUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Sanitized tool-call metadata (no secrets / cross-tenant ids).
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AgentActionProposalModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A pending write the merchant has not yet confirmed (US2/US3)."""

    __tablename__ = "agent_action_proposals"
    __table_args__ = {"schema": "public"}

    conversation_id: Mapped[PyUUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The store the proposal was built against. Confirm takes its store from the
    # URL, so without this a tenant with two stores could propose on A and
    # confirm at /stores/B/agent/confirm — the coupon lands on B.
    # Nullable for the handful of rows that predate the column; those are
    # refused rather than trusted.
    store_id: Mapped[PyUUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    diff: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    based_on_theme_version: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


class AgentAuditLogModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Immutable audit record of an applied (or rejected-at-apply) write."""

    __tablename__ = "agent_audit_logs"
    __table_args__ = {"schema": "public"}

    staff_id: Mapped[PyUUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[PyUUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    before_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    after_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
