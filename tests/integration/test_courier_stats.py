"""Tests for the courier stats aggregator (backend-023 / spec 013).

Pure-function tests against the aggregation logic, plus a small end-to-end
test that the rollup-table model + ON CONFLICT UPDATE upsert behaves as
expected on SQLite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.courier_stats_service import (
    RECOMMENDATION_MIN_SAMPLE,
    CarrierAggregate,
    ShipmentSnapshot,
    aggregate_shipments,
    can_recommend,
    rolling_window,
)

# ---------------------------------------------------------------------------
# Pure-function aggregation tests
# ---------------------------------------------------------------------------


def _ship(
    carrier: str = "bosta",
    status: str = "delivered",
    cod_amount: int = 0,
    cod_collected: bool = False,
    shipped_at: datetime | None = None,
    delivered_at: datetime | None = None,
) -> ShipmentSnapshot:
    return ShipmentSnapshot(
        carrier=carrier,
        status=status,
        cod_amount=cod_amount,
        cod_collected=cod_collected,
        shipped_at=shipped_at,
        delivered_at=delivered_at,
    )


class TestAggregateShipments:
    """Spec 013 FR-001 — per-carrier rollup is a pure reduction."""

    def test_empty_input_returns_empty_dict(self):
        assert aggregate_shipments([]) == {}

    def test_groups_by_carrier(self):
        result = aggregate_shipments([
            _ship(carrier="bosta", status="delivered"),
            _ship(carrier="mylerz", status="delivered"),
            _ship(carrier="bosta", status="returned"),
        ])
        assert set(result.keys()) == {"bosta", "mylerz"}
        assert result["bosta"].total_shipments == 2
        assert result["mylerz"].total_shipments == 1

    def test_buckets_terminal_statuses(self):
        result = aggregate_shipments([
            _ship(status="delivered"),
            _ship(status="delivered"),
            _ship(status="returned"),
            _ship(status="rto"),  # Alias of returned
            _ship(status="failed"),
            _ship(status="cancelled"),
            _ship(status="in_transit"),  # Non-terminal
            _ship(status="out_for_delivery"),  # Non-terminal
        ])
        agg = result["bosta"]
        assert agg.delivered_count == 2
        assert agg.returned_count == 2
        assert agg.failed_count == 2
        assert agg.in_progress_count == 2
        assert agg.total_shipments == 8

    def test_delivery_success_rate_excludes_in_progress(self):
        """Spec 013 — rate uses only terminal shipments as the denominator."""
        result = aggregate_shipments([
            _ship(status="delivered"),
            _ship(status="delivered"),
            _ship(status="returned"),
            _ship(status="in_transit"),  # Should NOT shrink the denominator
            _ship(status="in_transit"),
        ])
        # 2 delivered + 1 returned = 3 terminal; rate = 2/3
        assert pytest.approx(result["bosta"].delivery_success_rate, 0.001) == 2 / 3

    def test_delivery_success_rate_none_when_no_terminal(self):
        result = aggregate_shipments([
            _ship(status="in_transit"),
            _ship(status="created"),
        ])
        # Avoid divide-by-zero; rate is None on a fresh store with no
        # terminal shipments yet.
        assert result["bosta"].delivery_success_rate is None

    def test_cod_collection_rate(self):
        result = aggregate_shipments([
            _ship(cod_amount=10000, cod_collected=True),
            _ship(cod_amount=15000, cod_collected=True),
            _ship(cod_amount=20000, cod_collected=False),
            _ship(cod_amount=0, cod_collected=False),  # Not COD; excluded
        ])
        agg = result["bosta"]
        assert agg.cod_total_count == 3
        assert agg.cod_collected_count == 2
        assert pytest.approx(agg.cod_collection_rate, 0.001) == 2 / 3

    def test_avg_delivery_hours_computed_from_shipped_to_delivered(self):
        shipped = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
        result = aggregate_shipments([
            _ship(
                status="delivered",
                shipped_at=shipped,
                delivered_at=shipped + timedelta(hours=24),
            ),
            _ship(
                status="delivered",
                shipped_at=shipped,
                delivered_at=shipped + timedelta(hours=48),
            ),
        ])
        # Average of 24h + 48h = 36h.
        assert result["bosta"].avg_delivery_hours == 36.0

    def test_avg_delivery_hours_none_when_no_durations(self):
        result = aggregate_shipments([_ship(status="returned")])
        assert result["bosta"].avg_delivery_hours is None

    def test_negative_or_zero_durations_excluded(self):
        """Bad data (delivered_at before shipped_at) is silently ignored."""
        shipped = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
        result = aggregate_shipments([
            _ship(
                status="delivered",
                shipped_at=shipped,
                delivered_at=shipped - timedelta(hours=1),  # before shipped
            ),
            _ship(
                status="delivered",
                shipped_at=shipped,
                delivered_at=shipped + timedelta(hours=10),
            ),
        ])
        # Only the valid 10h duration counts.
        assert result["bosta"].avg_delivery_hours == 10.0


# ---------------------------------------------------------------------------
# Rolling-window helper
# ---------------------------------------------------------------------------


class TestRollingWindow:
    def test_default_30d_window_ends_today(self):
        end = date(2026, 5, 11)
        start, e = rolling_window(end=end)
        assert e == end
        assert (e - start).days == 30

    def test_custom_window_size(self):
        end = date(2026, 5, 11)
        start, e = rolling_window(end=end, days=7)
        assert (e - start).days == 7


# ---------------------------------------------------------------------------
# Recommendation gating (spec 013 FR-002 — ≥30 sample minimum)
# ---------------------------------------------------------------------------


class TestRecommendationGate:
    def test_below_minimum_sample_blocks(self):
        agg = CarrierAggregate(
            carrier="bosta",
            total_shipments=RECOMMENDATION_MIN_SAMPLE - 1,
            delivered_count=20,
        )
        assert can_recommend(agg) is False

    def test_at_minimum_sample_allows(self):
        agg = CarrierAggregate(
            carrier="bosta",
            total_shipments=RECOMMENDATION_MIN_SAMPLE,
            delivered_count=25,
        )
        assert can_recommend(agg) is True

    def test_minimum_sample_constant_pinned(self):
        # Spec 013 FR-002 requires ≥30. If this changes, spec needs amending.
        assert RECOMMENDATION_MIN_SAMPLE == 30


# ---------------------------------------------------------------------------
# DB-backed upsert idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courier_stats_upsert_is_idempotent(test_session: AsyncSession):
    """The (store_id, carrier, period_start) PK + ON CONFLICT UPDATE means
    re-running the rollup task on the same window updates in place rather
    than creating duplicate rows. This is the load-bearing idempotency
    contract for the nightly task.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.courier_stats import (
        CourierStatsModel,
    )

    store_id = uuid4()
    tenant_id = uuid4()
    period_start = date(2026, 4, 11)
    period_end = date(2026, 5, 11)
    now = datetime.now(UTC)

    base_values = {
        "tenant_id": tenant_id,
        "store_id": store_id,
        "carrier": "bosta",
        "period_start": period_start,
        "period_end": period_end,
        "total_shipments": 10,
        "delivered_count": 8,
        "returned_count": 2,
        "failed_count": 0,
        "in_progress_count": 0,
        "cod_collected_count": 8,
        "cod_total_count": 10,
        "delivery_success_rate": 0.8,
        "cod_collection_rate": 0.8,
        "avg_delivery_hours": 24.0,
        "last_refreshed_at": now,
    }

    stmt = pg_insert(CourierStatsModel).values(**base_values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["store_id", "carrier", "period_start"],
        set_={
            "total_shipments": base_values["total_shipments"],
            "delivered_count": base_values["delivered_count"],
            "last_refreshed_at": now,
        },
    )
    await test_session.execute(stmt)
    await test_session.flush()

    # Run again with bumped counts — same key, should UPDATE not INSERT.
    bumped = {
        **base_values,
        "total_shipments": 15,
        "delivered_count": 13,
    }
    stmt2 = pg_insert(CourierStatsModel).values(**bumped)
    stmt2 = stmt2.on_conflict_do_update(
        index_elements=["store_id", "carrier", "period_start"],
        set_={
            "total_shipments": bumped["total_shipments"],
            "delivered_count": bumped["delivered_count"],
            "last_refreshed_at": now,
        },
    )
    await test_session.execute(stmt2)
    await test_session.flush()

    # Exactly ONE row exists for this (store, carrier, period).
    rows = await test_session.execute(
        select(CourierStatsModel).where(
            CourierStatsModel.store_id == store_id,
            CourierStatsModel.carrier == "bosta",
            CourierStatsModel.period_start == period_start,
        )
    )
    rows_list = list(rows.scalars().all())
    assert len(rows_list) == 1
    assert rows_list[0].total_shipments == 15
    assert rows_list[0].delivered_count == 13
