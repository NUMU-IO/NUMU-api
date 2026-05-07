"""Promotion database models for the unified Offers/Promotions system.

Five tables back the offers-v2 feature:

* `PromotionModel` — master record. One row per merchant-configured offer.
* `PromotionDisplayModel` — when/how the promo is shown (triggers,
  frequency, page/device targeting). Multiple rows per promotion.
* `PromotionTargetModel` — who the promo applies to (audience, product,
  category, customer tag, geo). Multiple rows per promotion.
* `PromotionTranslationModel` — bilingual (en/ar) copy keyed by locale.
* `PromotionEventModel` — append-only event log (impression, click,
  dismiss, redeem, convert) that powers analytics.
* `PromotionDismissalModel` — per-customer / per-anonymous-visitor
  suppression so the same shopper isn't nagged.

Every table carries a `tenant_id` discriminator and is wrapped by RLS at
the database layer. Surface-specific config lives polymorphically in
JSONB columns; validation is the application-layer's job.
"""

from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums.promotion_enums import (
    DisplayFrequency,
    DisplayTrigger,
    PromotionEventType,
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


def _enum_col(py_enum: type, name: str) -> Enum:
    """Build a SQLAlchemy `Enum` column bound to an existing Postgres ENUM.

    Mirrors the pattern used by the existing Coupon / Order models:
    `create_type=False` so the migration owns the type definition,
    `values_callable` so we send the lower-case string value rather than
    the Python enum member name.
    """
    return Enum(
        py_enum,
        name=name,
        schema="public",
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


_promotion_surface_enum = _enum_col(PromotionSurface, "promotion_surface_enum")
_promotion_status_enum = _enum_col(PromotionStatus, "promotion_status_enum")
_display_trigger_enum = _enum_col(DisplayTrigger, "display_trigger_enum")
_display_frequency_enum = _enum_col(DisplayFrequency, "display_frequency_enum")
_target_kind_enum = _enum_col(TargetKind, "target_kind_enum")
_event_type_enum = _enum_col(PromotionEventType, "event_type_enum")


class PromotionModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Master promotion record — one per merchant-configured offer."""

    __tablename__ = "promotions"
    __table_args__ = (
        Index("ix_promotions_store_id", "store_id"),
        Index("ix_promotions_surface", "surface"),
        Index("ix_promotions_ends_at", "ends_at"),
        Index("ix_promotions_coupon_id", "coupon_id"),
        Index(
            "ix_promotions_tenant_store_status_surface",
            "tenant_id",
            "store_id",
            "status",
            "surface",
        ),
        Index(
            "ix_promotions_store_status_ends_at",
            "store_id",
            "status",
            "ends_at",
        ),
        CheckConstraint(
            "surface != 'discount_code' OR coupon_id IS NOT NULL",
            name="ck_promotions_discount_code_has_coupon",
        ),
        CheckConstraint(
            "surface = 'discount_code' OR coupon_id IS NULL",
            name="ck_promotions_non_code_has_no_coupon",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_promotions_window_valid",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    surface: Mapped[str] = mapped_column(_promotion_surface_enum, nullable=False)
    status: Mapped[str] = mapped_column(
        _promotion_status_enum, nullable=False, server_default="draft"
    )
    coupon_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.coupons.id", ondelete="SET NULL"),
        nullable=True,
    )
    discount_rule: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'"
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # No SQLAlchemy relationships — child rows are managed via dedicated
    # repositories, and the DB FKs (`ON DELETE CASCADE`) handle cleanup.
    # Avoiding ORM-managed collections sidesteps cascade-driven greenlet
    # issues during flush in the async session.

    def __repr__(self) -> str:
        return (
            f"<PromotionModel(id={self.id}, name={self.name}, surface={self.surface})>"
        )


class PromotionDisplayModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Trigger / frequency / page-target rules for a promotion."""

    __tablename__ = "promotion_displays"
    __table_args__ = (
        Index("ix_promotion_displays_promotion_id", "promotion_id"),
        Index(
            "ix_promotion_displays_promo_enabled",
            "promotion_id",
            "is_enabled",
        ),
        {"schema": "public"},
    )

    promotion_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.promotions.id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(_display_trigger_enum, nullable=False)
    trigger_value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'"
    )
    frequency: Mapped[str] = mapped_column(_display_frequency_enum, nullable=False)
    pages: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'"
    )
    device_targets: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default='\'["desktop","mobile"]\''
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class PromotionTargetModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Audience / product / category / tag / geo targeting rule."""

    __tablename__ = "promotion_targets"
    __table_args__ = (
        Index("ix_promotion_targets_promotion_id", "promotion_id"),
        {"schema": "public"},
    )

    promotion_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.promotions.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_kind: Mapped[str] = mapped_column(_target_kind_enum, nullable=False)
    target_value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'"
    )
    inclusion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class PromotionTranslationModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Bilingual (en/ar) translatable copy for a promotion."""

    __tablename__ = "promotion_translations"
    __table_args__ = (
        Index("ix_promotion_translations_promotion_id", "promotion_id"),
        UniqueConstraint(
            "promotion_id",
            "locale",
            name="uq_promotion_translations_promo_locale",
        ),
        {"schema": "public"},
    )

    promotion_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.promotions.id", ondelete="CASCADE"),
        nullable=False,
    )
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'"
    )


class PromotionEventModel(Base, UUIDMixin, TenantMixin):
    """Append-only event log for promotion analytics.

    No `updated_at` — events are immutable. `occurred_at` carries a BRIN
    index (declared in the migration) since inserts arrive in time order.
    """

    __tablename__ = "promotion_events"
    __table_args__ = (
        Index("ix_promotion_events_store_id", "store_id"),
        Index("ix_promotion_events_promotion_id", "promotion_id"),
        Index(
            "ix_promotion_events_promo_type_time",
            "promotion_id",
            "event_type",
            "occurred_at",
        ),
        Index(
            "ix_promotion_events_store_time",
            "store_id",
            "occurred_at",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    promotion_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.promotions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(_event_type_enum, nullable=False)
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    discount_amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # `metadata` is reserved on SQLAlchemy declarative bases. Map the
    # Python attribute to a different name while keeping the DB column
    # name `metadata` (matches the data-model spec & migration).
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="'{}'"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class PromotionDismissalModel(Base, UUIDMixin, TenantMixin):
    """Suppression record so a shopper isn't shown the same promo twice.

    Exactly one of `customer_id` or `visitor_token` is set per row
    (CHECK constraint enforced in the migration). Two partial unique
    indexes prevent duplicate dismissals for the same subject.
    """

    __tablename__ = "promotion_dismissals"
    __table_args__ = (
        Index("ix_promotion_dismissals_promotion_id", "promotion_id"),
        CheckConstraint(
            "(customer_id IS NOT NULL) <> (visitor_token IS NOT NULL)",
            name="ck_promotion_dismissals_subject_xor",
        ),
        {"schema": "public"},
    )

    promotion_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.promotions.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="CASCADE"),
        nullable=True,
    )
    visitor_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
