"""Courier delivery statistics rollup (backend-023 / spec 013).

Per-store, per-carrier, per-period (rolling 30-day) aggregate of shipment
outcomes. Computed nightly by ``courier_stats_tasks.refresh_courier_stats``;
read by the merchant dashboard's Courier Intelligence section.

Composite PK ``(store_id, carrier, period_start)`` means one row per
store per carrier per rolling-window snapshot. Updates are atomic via
``INSERT ... ON CONFLICT UPDATE``.

The v1 schema aggregates only by ``(carrier, period)``. The
city/governorate dimension from spec 013 FR-001 is a follow-up; the
``period_end`` column is reserved for that future expansion.
"""

from datetime import date, datetime
from uuid import UUID as PyUUID

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, TimestampMixin


class CourierStatsModel(Base, TenantMixin, TimestampMixin):
    """Per-store / per-carrier / per-period delivery outcome rollup."""

    __tablename__ = "courier_stats"
    __table_args__ = (
        Index(
            "ix_courier_stats_store_period",
            "store_id",
            "period_start",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    carrier: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
        comment="Carrier slug — bosta, mylerz, jt, etc.",
    )
    period_start: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="Inclusive start of the rolling-30d window (snapshot date - 30d)",
    )
    period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Exclusive end (snapshot date)",
    )
    total_shipments: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    delivered_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    returned_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    in_progress_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    # Spec 013 FR-001 — sample-size gate (recommendations require ≥30).
    # Stored on the row so the read endpoint can apply it without joining.
    cod_collected_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    cod_total_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    # Pre-computed rates persisted as `Numeric` so the API doesn't have to
    # compute them on every read. Both are 0.0..1.0 (multiply by 100 for %).
    delivery_success_rate: Mapped[float | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
    )
    cod_collection_rate: Mapped[float | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
    )
    # Average hours from `shipped_at` → `delivered_at` for delivered shipments
    # in the window. NULL if no deliveries yet.
    avg_delivery_hours: Mapped[float | None] = mapped_column(
        Numeric(7, 2),
        nullable=True,
    )
    last_refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
