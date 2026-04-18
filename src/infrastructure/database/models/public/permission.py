"""Permission catalog database model (public schema).

This is a seeded catalog that defines all available permissions in the system.
Permissions are immutable per deploy and include domain, action, scope_type, and risk_level.
"""

from enum import StrEnum

from sqlalchemy import Boolean, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class PermissionScopeType(StrEnum):
    """Scope type for permission resolution."""

    ALL = "all"
    OWN = "own"
    ASSIGNED = "assigned"
    RESOURCE = "resource"


class PermissionRiskLevel(StrEnum):
    """Risk level for permissions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PermissionModel(Base, UUIDMixin):
    """Permission catalog model.

    Permissions are seeded per deploy and define the available actions
    that can be granted to roles. They include domain, action, scope_type,
    risk_level, and optional dependencies.
    """

    __tablename__ = "permissions"
    __table_args__ = (
        Index("ix_permissions_code", "code", unique=True),
        Index("ix_permissions_domain", "domain"),
        {"schema": "public"},
    )

    code: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    qualifier: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scope_type: Mapped[PermissionScopeType] = mapped_column(
        Enum(PermissionScopeType, name="permissionscopetype", schema="public"),
        default=PermissionScopeType.ALL,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependencies: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list
    )
    risk_level: Mapped[PermissionRiskLevel] = mapped_column(
        Enum(PermissionRiskLevel, name="permissionrisklevel", schema="public"),
        default=PermissionRiskLevel.LOW,
        nullable=False,
    )
    is_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    plugin_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    role_permissions = relationship(
        "RolePermissionModel",
        back_populates="permission",
    )

    def __repr__(self) -> str:
        return f"<PermissionModel(code={self.code})>"
