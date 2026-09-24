"""Entitlements, release flags and usage counters (public schema).

Platform tables, like the wallet and billing ones: they decide what a merchant
may use, so only the API writes them and they carry explicit tenant keys.
Value shape (true/false, a non-negative int, or "unlimited") is enforced by
CHECK constraints in the migration; agreement with the feature's kind is
enforced by ``check_value`` in the service.
"""

from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class FeatureModel(Base, TimestampMixin):
    """The catalog. Rows are created by migrations, never by the admin UI:
    a feature without code behind it is a switch that does nothing."""

    __tablename__ = "features"
    __table_args__ = {"schema": "public"}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_ar: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(40))
    #: boolean | limit
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    default_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    #: limits only. count = COUNT the real rows; counter = usage_counters.
    usage: Mapped[str | None] = mapped_column(String(10))
    #: day | month | None (lifetime / current total)
    period: Mapped[str | None] = mapped_column(String(10))
    #: hard = refuse past the limit; soft = allow, record, notify.
    enforcement: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="hard"
    )
    unit: Mapped[str | None] = mapped_column(String(20))
    #: The global kill switch.
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    disabled_reason: Mapped[str | None] = mapped_column(Text)


class PlanEntitlementModel(Base):
    """What a plan grants. Add-on bundles use ``plan_key = 'addon:<app slug>'``."""

    __tablename__ = "plan_entitlements"
    __table_args__ = {"schema": "public"}

    plan_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    feature_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.features.key", ondelete="CASCADE"),
        primary_key=True,
    )
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntitlementOverrideModel(Base, UUIDMixin):
    """A per-tenant exception. Absolute: it replaces the plan value, it does
    not add to it. At most one live row per (tenant, feature)."""

    __tablename__ = "entitlement_overrides"
    __table_args__ = (
        Index(
            "uq_entitlement_overrides_live",
            "tenant_id",
            "feature_key",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.features.key", ondelete="CASCADE"),
        nullable=False,
    )
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: support | sales | promotion | beta | contract | testing | migration
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )


class FeatureFlagModel(Base, TimestampMixin):
    """A release switch. Knows nothing about plans."""

    __tablename__ = "feature_flags"
    __table_args__ = {"schema": "public"}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(120))
    #: Display grouping only (admin "Rollout" panel); never read by the resolver.
    feature_key: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("public.features.key", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    rollout_percent: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )


class FeatureFlagTargetModel(Base):
    __tablename__ = "feature_flag_targets"
    __table_args__ = (
        Index("ix_feature_flag_targets_tenant", "tenant_id"),
        {"schema": "public"},
    )

    flag_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.feature_flags.key", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UsageCounterModel(Base):
    """Consumable meters. One row per tenant, feature and period bucket."""

    __tablename__ = "usage_counters"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    feature_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.features.key", ondelete="CASCADE"),
        primary_key=True,
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
