"""Merchant metric targets (public schema with tenant_id discriminator).

One row per (store, metric, period): the merchant's goal for the current
recurring period — e.g. "EGP 150,000 revenue per month". Values are stored
as integers in the metric's smallest unit so no floats touch money:

- ``revenue`` / ``aov``  → cents
- ``orders``             → count
- ``conversion``         → basis points (2.5% == 250)

Progress/pace are computed at read time from the analytics rollups — the
target row itself stores only the goal, so historical actuals never need
backfilling when a target changes.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    UUIDMixin,
)

METRIC_TARGET_METRICS = ("revenue", "orders", "aov", "conversion")
METRIC_TARGET_PERIODS = ("month", "quarter")


class MetricTargetModel(Base, UUIDMixin, TenantMixin):
    """Merchant-defined goal for one metric over a recurring period."""

    __tablename__ = "metric_targets"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "metric", "period", name="uq_metric_targets_store_metric_period"
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Plain strings (validated at the API layer) rather than PG enums —
    # the orderstatus enum's lowercase-value migration pain is a lesson
    # this table doesn't need to relearn.
    metric: Mapped[str] = mapped_column(String(20), nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False, default="month")
    target_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<MetricTargetModel(store={self.store_id}, {self.metric}/"
            f"{self.period}={self.target_value})>"
        )
