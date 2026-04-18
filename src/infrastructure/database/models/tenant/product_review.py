"""Product review database model (tenant-scoped)."""

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
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
