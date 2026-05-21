"""Payment reconciliation database models (public schema)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class PaymentReconciliationRunModel(Base, UUIDMixin, TimestampMixin):
    """One reconciliation pass covering a gateway and time window, scoped to a store."""

    __tablename__ = "payment_reconciliation_runs"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    gateway: Mapped[str] = mapped_column(String(50), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    total_orders_checked: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    total_transactions_checked: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    mismatches_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_amount_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    actual_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReconciliationMismatchModel(Base, UUIDMixin, TimestampMixin):
    """Individual discrepancy found within a reconciliation run."""

    __tablename__ = "reconciliation_mismatches"
    __table_args__ = {"schema": "public"}

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    mismatch_type: Mapped[str] = mapped_column(String(50), nullable=False)
    order_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    gateway_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    expected_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gateway: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
