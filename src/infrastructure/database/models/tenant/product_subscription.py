"""Back-in-stock subscription model (Phase 3.5)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ProductSubscriptionModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Customer back-in-stock notification request.

    Sweep task uses the `(notified_at IS NULL)` partial index to scan
    only pending rows; the unique constraint on `(product_id,
    variant_id, email)` keeps a customer from spamming the queue with
    repeat subscriptions for the same SKU.
    """

    __tablename__ = "product_subscriptions"
    __table_args__ = (
        # Idempotent subscribe: clicking "Notify me" twice on the same
        # product yields a single pending row, not two.
        UniqueConstraint(
            "product_id", "variant_id", "email", name="uq_product_subscription_target"
        ),
        # Sweep-friendly partial index: only rows still awaiting
        # delivery cost an index entry. Once notified_at fills in, the
        # row drops off the hot index.
        Index(
            "ix_product_subscriptions_pending",
            "store_id",
            "product_id",
            postgresql_where=text("notified_at IS NULL"),
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
    email: Mapped[str] = mapped_column(
        String(254),
        nullable=False,
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
