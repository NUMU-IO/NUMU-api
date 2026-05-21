"""Wishlist DB model (Phase 4.5)."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WishlistItemModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "wishlist_items"
    __table_args__ = (
        # Idempotent: a customer (or guest session) can have a single
        # row per (product, variant). Variant null + variant value are
        # distinct in Postgres uniqueness — a customer can wishlist
        # both "Hoodie (any variant)" and "Hoodie / Black".
        UniqueConstraint(
            "customer_id",
            "session_id",
            "product_id",
            "variant_id",
            name="uq_wishlist_target",
        ),
        Index("ix_wishlist_customer", "customer_id"),
        Index("ix_wishlist_session", "session_id"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
