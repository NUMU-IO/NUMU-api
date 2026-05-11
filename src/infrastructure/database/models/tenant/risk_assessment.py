"""Risk assessment model — stores risk scores for orders."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class RiskAssessmentModel(Base, UUIDMixin, TimestampMixin):
    """Risk score for a single order."""

    __tablename__ = "risk_assessments"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
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
    score_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="'preliminary'",
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
    scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Backend-022: positive trust signals
    # Populated only on `score_type='final'`; preliminary scores leave NULL.
    customer_trust: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    trust_tier: Mapped[str | None] = mapped_column(
        String(10),  # 'none' | 'new' | 'bronze' | 'silver' | 'gold'
        nullable=True,
    )
    negative_adjustment_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    # HMAC hash of the customer's phone, persisted so downstream signal-write
    # paths (trust signal handler, future spec 010 contribution) don't have to
    # re-derive it from the raw phone (which is never stored on this row per
    # constitution Principle II).
    customer_phone_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
