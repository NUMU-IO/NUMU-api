"""Tenant membership database model (public schema).

Joins users to tenants with roles and permission version tracking.
This is the core of the multi-tenant permission system.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin
from src.infrastructure.database.models.public.role import RoleModel


class MembershipStatus(StrEnum):
    """Status of tenant membership."""

    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class TenantMembershipModel(Base, UUIDMixin, TimestampMixin):
    """Tenant membership model - user × tenant join.

    This is the core of the multi-tenant permission system. A user can have
    multiple memberships (different roles in different tenants).
    """

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        Index(
            "ix_tenant_memberships_user_tenant",
            "user_id",
            "tenant_id",
            unique=True,
        ),
        Index("ix_tenant_memberships_tenant_status", "tenant_id", "status"),
        Index("ix_tenant_memberships_user_id", "user_id"),
        {"schema": "public"},
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membershipstatus", schema="public"),
        default=MembershipStatus.INVITED,
        nullable=False,
    )
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invited_by_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    permission_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    two_factor_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship(
        "UserModel",
        back_populates="staff_memberships",
        foreign_keys=[user_id],
        lazy="selectin",
    )
    roles: Mapped[list[RoleModel]] = relationship(
        "RoleModel",
        secondary="public.membership_roles",
        back_populates="memberships",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<TenantMembershipModel(user_id={self.user_id}, tenant_id={self.tenant_id})>"
