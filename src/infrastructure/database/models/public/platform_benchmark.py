"""Platform benchmark aggregates (public schema).

Written only by the platform-level benchmark task; contains percentile
aggregates per (period, segment, metric) cell — never store-level rows.
k-anonymity (n_stores ≥ 10) is enforced at read time so the table can
hold small cells while the platform grows without ever publishing them.
"""

from sqlalchemy import Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class PlatformBenchmarkModel(Base, UUIDMixin, TimestampMixin):
    """One percentile cell: metric × segment × period.

    ``segment_key`` is hierarchical text (``all``, ``size:51-300``,
    ``industry:fashion|size:51-300``) so readers can fall back up the
    hierarchy when their exact cell is below the k-anonymity floor.
    """

    __tablename__ = "platform_benchmarks"
    __table_args__ = (
        UniqueConstraint(
            "period", "segment_key", "metric", name="uq_platform_benchmark_cell"
        ),
        {"schema": "public"},
    )

    # Calendar month the metrics were computed over, e.g. "2026-07".
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    segment_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    # Winsorized (P5/P95) percentiles. Units depend on the metric
    # (cents for aov_cents, percent for *_pct) — mirrors the metric name.
    p25: Mapped[float] = mapped_column(Float, nullable=False)
    p50: Mapped[float] = mapped_column(Float, nullable=False)
    p75: Mapped[float] = mapped_column(Float, nullable=False)
    n_stores: Mapped[int] = mapped_column(Integer, nullable=False)
