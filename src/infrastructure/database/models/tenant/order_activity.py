"""Order activity database model.

Holds the merchant-visible per-order activity stream (staff comments + system
events). Distinct from `audit_logs`, which is forensic and cross-tenant.
Append-only: no edit/delete in Phase 1.
"""

from uuid import UUID as PyUUID

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.order_activity import OrderActivityKind
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class OrderActivityModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Order activity row: staff comment or persisted system event."""

    __tablename__ = "order_activities"
    __table_args__ = {"schema": "public"}

    order_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable + no FK so a removed staff user's comments remain readable.
    user_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    kind: Mapped[OrderActivityKind] = mapped_column(
        Enum(OrderActivityKind, name="orderactivitykind", schema="public"),
        nullable=False,
    )
    event_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    activity_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict
    )

    def __repr__(self) -> str:
        return (
            f"<OrderActivityModel(id={self.id}, order_id={self.order_id}, "
            f"kind={self.kind})>"
        )
