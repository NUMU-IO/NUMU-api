"""Analytics daily rollup database model.

Pre-aggregated daily metrics per store for fast analytics queries.
Populated nightly by Celery task; replaces N+1 per-day queries.
"""

from datetime import date, datetime
from uuid import UUID as PyUUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class AnalyticsDailyRollupModel(Base, UUIDMixin, TenantMixin):
    """Pre-aggregated daily analytics per store.

    Populated by the nightly rollup Celery task.
    Each row represents one store's metrics for one calendar day (UTC).
    """

    __tablename__ = "analytics_daily_rollups"
    __table_args__ = (
        Index(
            "ix_rollup_store_date",
            "store_id",
            "rollup_date",
            unique=True,
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rollup_date: Mapped[date] = mapped_column(Date, nullable=False)

    # ── Revenue ──
    total_revenue_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    paid_orders: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_orders: Mapped[int] = mapped_column(Integer, default=0)
    avg_order_value_cents: Mapped[int] = mapped_column(Integer, default=0)

    # ── Customers ──
    new_customers: Mapped[int] = mapped_column(Integer, default=0)
    returning_customers: Mapped[int] = mapped_column(Integer, default=0)

    # ── Traffic ──
    total_page_views: Mapped[int] = mapped_column(Integer, default=0)
    unique_visitors: Mapped[int] = mapped_column(Integer, default=0)

    # ── COD ──
    cod_orders: Mapped[int] = mapped_column(Integer, default=0)
    cod_delivered: Mapped[int] = mapped_column(Integer, default=0)
    cod_rejected: Mapped[int] = mapped_column(Integer, default=0)

    # ── Refunds ──
    refund_count: Mapped[int] = mapped_column(Integer, default=0)
    refund_amount_cents: Mapped[int] = mapped_column(Integer, default=0)

    # ── Breakdowns (JSONB) ──
    # [{product_id, name, sku, quantity, revenue}]
    top_products_json: Mapped[list | None] = mapped_column(JSONB, default=list)
    # [{location, revenue, orders}]
    revenue_by_location_json: Mapped[list | None] = mapped_column(JSONB, default=list)
    # [{source, medium, orders, revenue}]
    traffic_sources_json: Mapped[list | None] = mapped_column(JSONB, default=list)

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AnalyticsDailyRollup(store={self.store_id}, date={self.rollup_date})>"
