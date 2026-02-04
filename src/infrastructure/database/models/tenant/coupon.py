"""Coupon database model (public schema with tenant_id discriminator)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.coupon import CouponType
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class CouponModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Coupon database model with tenant_id discriminator."""

    __tablename__ = "coupons"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    coupon_type: Mapped[CouponType] = mapped_column(
        Enum(CouponType, name="coupontype", schema="public"),
        nullable=False,
    )
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    min_order_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    max_discount_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    usage_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    store = relationship("StoreModel", lazy="selectin")

    def __repr__(self) -> str:
        return f"<CouponModel(id={self.id}, code={self.code})>"
