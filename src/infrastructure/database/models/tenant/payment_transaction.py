"""Payment transaction model — detailed payment tracking."""

from datetime import datetime

from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class PaymentTransactionModel(Base, UUIDMixin, TimestampMixin):
    """Detailed payment transaction for analytics."""

    __tablename__ = "payment_transactions"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    channel: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    gateway: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    display_name: Mapped[str | None] = mapped_column(
        String(100),
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
    )
    failure_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    gateway_transaction_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
