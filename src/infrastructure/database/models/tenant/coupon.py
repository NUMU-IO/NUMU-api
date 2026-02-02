"""Coupon database model (public schema with tenant_id discriminator)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.coupon import DiscountType
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, TimestampMixin, UUIDMixin


class CouponModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Coupon database model with tenant_id discriminator."""

    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("store_id", "code", name="uq_coupons_store_code"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_type: Mapped[DiscountType] = mapped_column(
        Enum(DiscountType, name="discounttype", schema="public"),
        nullable=False,
    )
    discount_value: Mapped[int] = mapped_column(Integer, nullable=False)
    min_order_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_discount_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_uses_per_customer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Relationships
    store = relationship("StoreModel", back_populates="coupons", lazy="selectin")

    def __repr__(self) -> str:
        return f"<CouponModel(id={self.id}, code={self.code})>"
