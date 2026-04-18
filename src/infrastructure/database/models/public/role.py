"""Role database model (public schema).

Roles bundle permissions and can be system templates (tenant_id NULL) or
tenant-owned. System roles are cloned per tenant on tenant creation.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class RoleModel(Base, UUIDMixin, TimestampMixin):
    """Role model for bundling permissions.

    Roles can be:
    - System templates (tenant_id NULL) - seeded at deploy
    - Tenant-owned (tenant_id set) - created by tenants
    - Owner role (is_owner=True) - special implicit role
    """

    __tablename__ = "roles"
    __table_args__ = (
        Index("ix_roles_tenant_slug", "tenant_id", "slug", unique=True),
        Index("ix_roles_tenant_id", "tenant_id"),
        {"schema": "public"},
    )

    tenant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cloned_from_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.roles.id"), nullable=True
    )
    created_by_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    permissions: Mapped[list["RolePermissionModel"]] = relationship(
        "RolePermissionModel",
        back_populates="role",
        lazy="selectin",
    )
    memberships = relationship(
        "TenantMembershipModel",
        secondary="public.membership_roles",
        back_populates="roles",
    )

    def __repr__(self) -> str:
        return f"<RoleModel(name={self.name}, tenant_id={self.tenant_id})>"


class RolePermissionModel(Base, TimestampMixin):
    """Many-to-many join table for roles and permissions."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        Index("ix_role_permissions_role_id", "role_id"),
        Index("ix_role_permissions_permission_id", "permission_id"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )
    role_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.permissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_qualifier: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    granted_by_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    role: Mapped["RoleModel"] = relationship("RoleModel", back_populates="permissions")
    permission: Mapped["PermissionModel"] = relationship(
        "PermissionModel",
        back_populates="role_permissions",
    )

    def __repr__(self) -> str:
        return f"<RolePermissionModel(role_id={self.role_id}, perm_id={self.permission_id})>"


from src.infrastructure.database.models.public.permission import (
    PermissionModel,  # noqa: E402,F401
)
