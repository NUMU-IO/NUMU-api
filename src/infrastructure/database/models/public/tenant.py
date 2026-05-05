"""Tenant database model (public schema)."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class TenantStatus(StrEnum):
    """Tenant status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING_SETUP = "pending_setup"


class TenantPlan(StrEnum):
    """Tenant subscription plan.

    ``FREE`` is **deprecated** as a public-facing plan. It is retained in the
    enum for legacy data and internal sandboxes only — new signups go to
    ``TRIAL`` (30-day free trial of Starter features) and convert to
    ``STARTER``/``PRO``/``ENTERPRISE`` afterward.
    """

    FREE = "free"  # deprecated — see plan.py module docs
    DEMO = "demo"  # internal sandbox: Try-a-Demo flow
    TRIAL = "trial"  # 30-day free trial; auto-transitions to read_only on expiry
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantLifecycleState(StrEnum):
    """Tenant lifecycle state machine.

    All ephemerality and billing state lives in this single column to avoid
    the "what if both is_demo and is_trial are true" class of bugs. The five
    valid transitions are:

    * ``demo`` → (cleanup task at 7d) → row deleted
    * ``trial`` → (trial_expiry_task at 30d, no conversion) → ``read_only``
    * ``trial`` → (subscribe) → ``active``
    * ``active`` → (cancel_subscription, OR failed renewal after dunning) → ``read_only``
    * ``read_only`` → (subscribe) → ``active``
    * ``read_only`` → (read_only_purge_task at delete_at) → row deleted
    """

    DEMO = "demo"
    TRIAL = "trial"
    ACTIVE = "active"
    READ_ONLY = "read_only"
    CANCELLED = "cancelled"


class TenantModel(Base, UUIDMixin, TimestampMixin):
    """Tenant model representing an organization/store owner in the platform.

    This model lives in the 'public' PostgreSQL schema and is used
    to track all tenants. The tenant_id is used as a discriminator
    in all tenant-scoped tables.
    """

    __tablename__ = "tenants"
    __table_args__ = (
        Index("ix_tenants_lifecycle_state", "lifecycle_state"),
        Index("ix_tenants_expires_at", "expires_at"),
        Index("ix_tenants_delete_at", "delete_at"),
        {"schema": "public"},
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subdomain: Mapped[str] = mapped_column(
        String(63), unique=True, index=True, nullable=False
    )
    owner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan: Mapped[str] = mapped_column(String(50), default="trial", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # ─── Lifecycle state machine (Stream 0.3 of NUMU plan) ───────────────
    lifecycle_state: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_only_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delete_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Demo flow specifics
    demo_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    demo_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Trial flow specifics
    trial_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_converted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    owner = relationship("UserModel", back_populates="owned_tenants", lazy="selectin")
    stores = relationship("StoreModel", back_populates="tenant", lazy="selectin")

    @property
    def schema_name(self) -> str:
        """Get the tenant's database schema name from settings."""
        if self.settings and "schema_name" in self.settings:
            return self.settings["schema_name"]
        # Fallback: derive from subdomain
        return f"tenant_{self.subdomain}"

    # ─── Lifecycle helpers ────────────────────────────────────────────────

    @property
    def is_demo(self) -> bool:
        return self.lifecycle_state == TenantLifecycleState.DEMO

    @property
    def is_on_trial(self) -> bool:
        return self.lifecycle_state == TenantLifecycleState.TRIAL

    @property
    def is_read_only(self) -> bool:
        return self.lifecycle_state == TenantLifecycleState.READ_ONLY

    @property
    def is_writable(self) -> bool:
        """True when the tenant can accept new orders / mutations."""
        return self.lifecycle_state in (
            TenantLifecycleState.DEMO,
            TenantLifecycleState.TRIAL,
            TenantLifecycleState.ACTIVE,
        )

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        # expires_at is stored timezone-aware
        return self.expires_at < datetime.now(UTC)

    @property
    def days_remaining(self) -> int | None:
        """Days until expiry, or None if no expiry set. Returns 0 if past expiry."""
        if not self.expires_at:
            return None
        delta = self.expires_at - datetime.now(UTC)
        return max(0, delta.days)

    def __repr__(self) -> str:
        return (
            f"<TenantModel(id={self.id}, subdomain={self.subdomain}, "
            f"state={self.lifecycle_state})>"
        )
