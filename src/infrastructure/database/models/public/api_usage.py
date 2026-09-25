"""Daily public-API usage per merchant, key, and endpoint.

Written only by the usage flush (``api_limits.flush_usage``) from Redis day
aggregates; there is no row per request. The composite key is the natural
grain, so re-flushing a day updates in place.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base


class ApiUsageDailyModel(Base):
    __tablename__ = "api_usage_daily"
    __table_args__ = (
        Index("ix_api_usage_daily_day", "day"),
        {"schema": "public"},
    )

    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    #: No FK: history outlives a deleted key.
    token_id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    method: Mapped[str] = mapped_column(String(8), primary_key=True)
    #: Route template, e.g. /api/v1/stores/{store_id}/orders
    route: Mapped[str] = mapped_column(String(200), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_4xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_5xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    throttled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms_sum: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
