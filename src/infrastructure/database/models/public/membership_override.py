"""Membership role and permission override models (public schema).

These tables provide fine-grained ALLOW/DENY overrides at the membership level,
allowing per-user exceptions to role-derived permissions.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin


class OverrideEffect(StrEnum):
    """Effect of permission override."""

    ALLOW = "allow"
    DENY = "deny"


class MembershipRoleModel(Base, TimestampMixin):
    """Many-to-many join table for memberships and roles."""

    __tablename__ = "membership_roles"
    __table_args__ = (
        Index("ix_membership_roles_membership_id", "membership_id"),
        Index("ix_membership_roles_role_id", "role_id"),
        {"schema": "public"},
    )

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    membership_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenant_memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_by_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<MembershipRoleModel(membership={self.membership_id}, role={self.role_id})>"


class MembershipOverrideModel(Base, TimestampMixin):
    """Permission override at membership level.

    Allows fine-grained ALLOW/DENY exceptions to role-derived permissions.
    DENY overrides always win over ALLOW, except for owner (owner short-circuit).
    """

    __tablename__ = "membership_permission_overrides"
    __table_args__ = (
        Index("ix_membership_overrides_membership", "membership_id", "permission_id"),
        {"schema": "public"},
    )

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    membership_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenant_memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.permissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    effect: Mapped[OverrideEffect] = mapped_column(
        Enum(OverrideEffect, name="overrideeffect", schema="public"),
        nullable=False,
    )
    scope_qualifier: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_by_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<MembershipOverrideModel(membership={self.membership_id}, perm={self.permission_id}, effect={self.effect})>"
