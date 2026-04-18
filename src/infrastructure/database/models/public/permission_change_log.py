"""Permission change log database model (public schema).

Structured audit log for permission changes with diffs, providing fast UI queries.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base


class PermissionChangeTargetType(StrEnum):
    """Target type for permission change."""

    ROLE = "role"
    MEMBERSHIP = "membership"
    OVERRIDE = "override"
    INVITATION = "invitation"


class PermissionChangeAction(StrEnum):
    """Action performed on permission target."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ROLE_ASSIGNED = "role_assigned"
    ROLE_REVOKED = "role_revoked"
    PERM_ADDED = "perm_added"
    PERM_REMOVED = "perm_removed"
    OVERRIDE_SET = "override_set"
    OVERRIDE_CLEARED = "override_cleared"
    OWNERSHIP_TRANSFERRED = "ownership_transferred"


class PermissionChangeLogModel(Base):
    """Structured permission change log.

    Provides queryable audit trail for permission changes with:
    - Diff structure (added/removed)
    - Actor and target info
    - Reason and IP context
    """

    __tablename__ = "permission_change_logs"
    __table_args__ = (
        Index("ix_permission_change_logs_tenant", "tenant_id", "created_at"),
        Index("ix_permission_change_logs_target", "target_type", "target_id"),
        Index("ix_permission_change_logs_actor", "actor_user_id"),
        {"schema": "public"},
    )

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    actor_user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    target_type: Mapped[PermissionChangeTargetType] = mapped_column(
        Enum(
            PermissionChangeTargetType,
            name="permissionchangetargettype",
            schema="public",
        ),
        nullable=False,
        index=True,
    )
    target_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    action: Mapped[PermissionChangeAction] = mapped_column(
        Enum(PermissionChangeAction, name="permissionchangeaction", schema="public"),
        nullable=False,
    )
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<PermissionChangeLogModel(target={self.target_type}, action={self.action})>"
