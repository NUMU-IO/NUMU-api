"""Upsell rule database model."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class UpsellRuleModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Upsell rule database model."""

    __tablename__ = "upsell_rules"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Trigger
    trigger_type: Mapped[str] = mapped_column(String(20), default="any", nullable=False)
    trigger_product_ids: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False
    )
    trigger_category_ids: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False
    )
    trigger_min_cart_value: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    # Offer
    offer_product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    discount_type: Mapped[str] = mapped_column(
        String(20), default="percentage", nullable=False
    )
    discount_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Limits
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Display
    headline_ar: Mapped[str] = mapped_column(
        String(200), default="عرض خاص لك! 🎁", nullable=False
    )
    headline_en: Mapped[str] = mapped_column(
        String(200), default="Special offer for you! 🎁", nullable=False
    )
    description_ar: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<UpsellRuleModel(id={self.id}, name={self.name})>"
