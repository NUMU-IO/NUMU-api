"""Merchant notification feed (public schema with tenant_id discriminator).

One row per merchant-facing event (new order, payment received, cart
abandoned, shipment delivered, kill-switch fired …). Backs the hub's
bell dropdown + Notifications page.

Like ``merchant_signals``, rows carry a machine ``kind`` + a ``data``
snapshot, NOT rendered copy — the hub renders bilingual titles from the
kind at read time, so copy fixes never need a data migration and the
customer name can be highlighted inline the way Zid does it.

``dedupe_key`` is unique per store so a replayed event (webhook retry,
double publish) can't produce two rows.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    UUIDMixin,
)

NOTIFICATION_CATEGORIES = (
    "orders",
    "abandoned_carts",
    "payments",
    "logistics",
    "system",
)


class MerchantNotificationModel(Base, UUIDMixin, TenantMixin):
    """One merchant-facing notification for a store."""

    __tablename__ = "merchant_notifications"
    __table_args__ = (
        Index(
            "ix_merchant_notifications_store_created",
            "store_id",
            "created_at",
        ),
        Index(
            "ix_merchant_notifications_store_category_created",
            "store_id",
            "category",
            "created_at",
        ),
        Index(
            "uq_merchant_notifications_store_dedupe",
            "store_id",
            "dedupe_key",
            unique=True,
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Plain strings validated at the application layer — no PG enums.
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    # e.g. "order.new", "payment.received", "cart.abandoned"
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Hub-relative deep link ("/orders/<id>").
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_important: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MerchantNotification {self.kind} store={self.store_id} "
            f"read={'y' if self.read_at else 'n'}>"
        )
