"""Courier statistics aggregation (backend-023 / spec 013).

Pure function ``aggregate_shipments`` reduces a list of shipment rows
into per-carrier rollup dicts. Kept stateless + DB-free so the
aggregation can be snapshot-tested without a SQLAlchemy session.

The repository / task layer wraps this with the actual SELECT + UPSERT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# Terminal statuses we group into outcome buckets. Strings come from
# `ShipmentModel.status` which is intentionally a String not Enum so
# new carriers can add new statuses without migrations.
DELIVERED_STATUSES = frozenset({"delivered"})
RETURNED_STATUSES = frozenset({"returned", "rto"})
FAILED_STATUSES = frozenset({"failed", "cancelled"})


@dataclass
class ShipmentSnapshot:
    """The minimal shipment fields the aggregator needs.

    Mirrors columns from ``ShipmentModel``; the repository layer maps
    SQLAlchemy rows into this dataclass before calling the aggregator
    so the aggregator stays decoupled from SQLAlchemy.
    """

    carrier: str
    status: str
    cod_amount: int
    cod_collected: bool
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None


@dataclass
class CarrierAggregate:
    """One per-carrier rollup row, ready for upsert into ``courier_stats``."""

    carrier: str
    total_shipments: int = 0
    delivered_count: int = 0
    returned_count: int = 0
    failed_count: int = 0
    in_progress_count: int = 0
    cod_collected_count: int = 0
    cod_total_count: int = 0
    _delivery_durations_hours: list[float] = field(default_factory=list)

    @property
    def delivery_success_rate(self) -> float | None:
        """Rate of `delivered / (delivered + returned + failed)`.

        Excludes in-progress shipments — only terminal states count.
        Returns None when no terminal shipments exist (avoids divide-by-zero
        and meaningless 0% on a fresh store).
        """
        terminal = self.delivered_count + self.returned_count + self.failed_count
        if terminal == 0:
            return None
        return self.delivered_count / terminal

    @property
    def cod_collection_rate(self) -> float | None:
        """Rate of `cod_collected / cod_total` for COD-bearing shipments."""
        if self.cod_total_count == 0:
            return None
        return self.cod_collected_count / self.cod_total_count

    @property
    def avg_delivery_hours(self) -> float | None:
        if not self._delivery_durations_hours:
            return None
        return sum(self._delivery_durations_hours) / len(self._delivery_durations_hours)


def aggregate_shipments(
    shipments: list[ShipmentSnapshot],
) -> dict[str, CarrierAggregate]:
    """Reduce a list of shipments into per-carrier aggregate dicts.

    Pure function — same input always yields same output. Used by the
    nightly Celery rollup task and by snapshot tests directly.
    """
    out: dict[str, CarrierAggregate] = {}
    for s in shipments:
        agg = out.setdefault(s.carrier, CarrierAggregate(carrier=s.carrier))
        agg.total_shipments += 1
        if s.status in DELIVERED_STATUSES:
            agg.delivered_count += 1
            if s.shipped_at and s.delivered_at:
                duration = (s.delivered_at - s.shipped_at).total_seconds() / 3600
                if duration > 0:
                    agg._delivery_durations_hours.append(duration)
        elif s.status in RETURNED_STATUSES:
            agg.returned_count += 1
        elif s.status in FAILED_STATUSES:
            agg.failed_count += 1
        else:
            agg.in_progress_count += 1
        # COD bucket — count any shipment with cod_amount > 0 as COD-bearing.
        if s.cod_amount > 0:
            agg.cod_total_count += 1
            if s.cod_collected:
                agg.cod_collected_count += 1
    return out


def rolling_window(end: date | None = None, days: int = 30) -> tuple[date, date]:
    """Compute the (period_start, period_end) tuple for a rolling N-day window.

    ``period_start`` is inclusive; ``period_end`` is exclusive.
    Defaults to a 30-day window ending today (UTC).
    """
    end_date = end or date.today()
    return (end_date - timedelta(days=days), end_date)


# Spec 013 FR-002 — recommendations require ≥30 sample shipments per cell
# to avoid statistical noise. Read-side helper exposed here so the API
# applies the same threshold.
RECOMMENDATION_MIN_SAMPLE = 30


def can_recommend(agg: CarrierAggregate) -> bool:
    return agg.total_shipments >= RECOMMENDATION_MIN_SAMPLE
