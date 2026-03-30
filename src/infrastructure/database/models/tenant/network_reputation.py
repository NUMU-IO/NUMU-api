"""Network reputation model — anonymized cross-merchant buyer intelligence."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class NetworkReputationModel(Base, UUIDMixin):
    """Aggregated cross-merchant buyer reputation keyed by hashed phone number.

    Privacy: phone_hash is HMAC-SHA256 of the E.164 phone number using
    PLATFORM_SECRET_SALT.  Raw phone numbers are NEVER stored.
    """

    __tablename__ = "network_reputation"
    __table_args__ = {"schema": "public"}

    phone_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    total_network_orders: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    total_network_rtos: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    total_successful_deliveries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    total_refunds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    contributing_store_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    network_risk_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    confidence_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="'low'",
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_order_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_rto_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
