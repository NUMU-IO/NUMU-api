"""Payment link session model — COD-to-Prepaid conversion tracking."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class PaymentLinkSessionModel(Base, UUIDMixin):
    """Tracks a COD-to-Prepaid payment conversion attempt.

    Created when a WhatsApp nudge is sent; completed when the customer
    pays via the standalone payment page (pay.numu.app).
    """

    __tablename__ = "payment_link_sessions"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    order_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    shopify_order_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    amount_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default="'EGP'",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="'pending'",
    )
    available_gateways: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
    )
    merchant_branding: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    gateway_used: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    gateway_transaction_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
