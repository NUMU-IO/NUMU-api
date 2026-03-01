"""Risk assessment model — stores risk scores for orders."""

from datetime import datetime

from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class RiskAssessmentModel(Base, UUIDMixin, TimestampMixin):
    """Risk score for a single order."""

    __tablename__ = "risk_assessments"
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
        index=True,
    )
    shopify_order_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    order_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    customer_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    customer_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    total_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default="'EGP'",
    )
    payment_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    risk_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    suggested_action: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    action_taken: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    action_taken_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    action_taken_by: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    factors: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'[]'",
    )
