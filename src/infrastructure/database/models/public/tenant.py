"""Tenant database model (public schema)."""

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class TenantStatus(str, Enum):
    """Tenant status enumeration."""
    
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING_SETUP = "pending_setup"


class TenantPlan(str, Enum):
    """Tenant subscription plan."""
    
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantModel(Base, UUIDMixin, TimestampMixin):
    """Tenant model representing an organization/store owner in the platform.
    
    This model lives in the 'public' PostgreSQL schema and is used
    to track all tenants. The tenant_id is used as a discriminator
    in all tenant-scoped tables.
    """
    __tablename__ = "tenants"
    __table_args__ = {"schema": "public"}

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subdomain: Mapped[str] = mapped_column(String(63), unique=True, index=True, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan: Mapped[str] = mapped_column(String(50), default="free", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Relationships
    owner = relationship("UserModel", back_populates="owned_tenants", lazy="selectin")
    stores = relationship("StoreModel", back_populates="tenant", lazy="selectin")

    def __repr__(self) -> str:
        return f"<TenantModel(id={self.id}, subdomain={self.subdomain})>"
