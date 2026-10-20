"""Product review database model (tenant-scoped)."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ProductReviewModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Product review — customer × product with rating & text."""

    __tablename__ = "product_reviews"
    __table_args__ = (
        Index("ix_product_reviews_product_id", "product_id"),
        Index("ix_product_reviews_store_id", "store_id"),
        Index(
            "ix_product_reviews_product_approved",
            "product_id",
            "is_approved",
        ),
        # One review per product per order through the Reviews app.
        Index(
            "uq_product_reviews_order_product",
            "store_id",
            "order_id",
            "product_id",
            unique=True,
            postgresql_where=text("order_id IS NOT NULL"),
            sqlite_where=text("order_id IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    helpful_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Reviews app (docs/Plans/APPS/03-reviews) works on this table directly.
    # A review core took on its own leaves these empty.
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Written through the buyer's own review link after delivery.
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Why the merchant turned it down (``spam`` · ``abuse`` · ``personal`` ·
    #: ``off_topic`` · ``duplicate``). A held review waiting for a decision has none.
    rejected_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reply_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The buyer's photos: ``[{"url", "thumb_url", "key", "thumb_key"}]``.
    media: Mapped[list | None] = mapped_column(JSONB, nullable=True)
