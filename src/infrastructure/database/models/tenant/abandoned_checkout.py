"""AbandonedCheckout database model."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class AbandonedCheckoutModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """In-progress or abandoned checkout cart, distinct from Order."""

    __tablename__ = "abandoned_checkouts"
    __table_args__ = (
        Index("idx_abandoned_checkouts_store_abandoned", "store_id", "abandoned_at"),
        Index(
            "idx_abandoned_checkouts_store_last_activity",
            "store_id",
            "last_activity_at",
        ),
        Index("idx_abandoned_checkouts_email", "email"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    line_items: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]", default=list
    )

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    shipping_address: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=dict
    )

    # Money fields (cents). Mirrors the Order model.
    subtotal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipping_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tax_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")

    coupon_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(100), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Lifecycle
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    abandoned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovery_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovered_order_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    def __repr__(self) -> str:
        return (
            f"<AbandonedCheckoutModel(id={self.id}, store_id={self.store_id}, "
            f"abandoned_at={self.abandoned_at})>"
        )
