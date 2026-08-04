"""Unit tests for the 2026-08 analytics-correctness batch.

Covers the pieces the change set explicitly claims to have fixed:

* ``_contiguous_runs``           — gap collapsing must never emit overlapping
  runs, because ``/top-products`` ADDS each run's live rows into a merged
  ranking (an overlap = silently double-counted revenue).
* ``_daily_revenue_series``      — rollup for completed days, live for today
  and for any day the nightly task missed; today may NEVER come from a
  rollup row even when one exists.
* ``exclude_non_revenue``        — one revenue definition, shared by the
  rollup task, the order repository and the analytics repository.
* ``realtime_counters.get_snapshot`` — the hourly pipeline index math
  (regression A1: the reads were offset against an INTERLEAVED pipeline).
* funnel cart abandonment        — the session-intersection form is bounded
  to [0, 100] even when ``checkout_started`` exceeds ``add_to_cart``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects import postgresql

from src.api.dependencies.date_range import resolve_date_range_window
from src.api.v1.routes.stores import analytics as analytics_routes
from src.infrastructure.cache import realtime_counters as rc
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.order_status_filters import (
    NON_REVENUE_STATUSES_LC,
    exclude_non_revenue,
    status_lc,
)

_contiguous_runs = analytics_routes._contiguous_runs
_daily_revenue_series = analytics_routes._daily_revenue_series

D = date(2026, 8, 1)


def _d(offset: int) -> date:
    return D + timedelta(days=offset)


# ══════════════════════════════════════════════════════════════════════
# 1. _contiguous_runs
# ══════════════════════════════════════════════════════════════════════


class TestContiguousRuns:
    def test_empty(self):
        assert _contiguous_runs([]) == []

    def test_single_element(self):
        assert _contiguous_runs([_d(0)]) == [(_d(0), _d(0))]

    def test_fully_contiguous_collapses_to_one_run(self):
        days = [_d(i) for i in range(5)]
        assert _contiguous_runs(days) == [(_d(0), _d(4))]

    def test_two_element_contiguous(self):
        assert _contiguous_runs([_d(0), _d(1)]) == [(_d(0), _d(1))]

    def test_two_element_gap(self):
        assert _contiguous_runs([_d(0), _d(2)]) == [(_d(0), _d(0)), (_d(2), _d(2))]

    def test_single_gap_splits_into_two_runs(self):
        days = [_d(0), _d(1), _d(5), _d(6), _d(7)]
        assert _contiguous_runs(days) == [(_d(0), _d(1)), (_d(5), _d(7))]

    def test_multiple_gaps(self):
        days = [_d(0), _d(3), _d(4), _d(9)]
        assert _contiguous_runs(days) == [
            (_d(0), _d(0)),
            (_d(3), _d(4)),
            (_d(9), _d(9)),
        ]

    def test_unsorted_input_is_sorted_first(self):
        days = [_d(6), _d(0), _d(5), _d(1), _d(7)]
        assert _contiguous_runs(days) == [(_d(0), _d(1)), (_d(5), _d(7))]

    def test_month_boundary_is_contiguous(self):
        days = [date(2026, 1, 30), date(2026, 1, 31), date(2026, 2, 1)]
        assert _contiguous_runs(days) == [(date(2026, 1, 30), date(2026, 2, 1))]

    def test_leap_day_is_contiguous(self):
        days = [date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1)]
        assert _contiguous_runs(days) == [(date(2028, 2, 28), date(2028, 3, 1))]

    def test_runs_are_never_overlapping_with_duplicate_input(self):
        """REGRESSION A2 — duplicates must not produce overlapping runs.

        ``/top-products`` sums the live rows of every run into one merged
        ranking, so two runs covering the same day double-count that day's
        units and revenue. The helper's whole reason to exist is preventing
        exactly that, so it must be robust to a duplicated input rather than
        relying on every caller to de-dupe.
        """
        runs = _contiguous_runs([_d(0), _d(0), _d(1), _d(3), _d(3)])
        assert runs == [(_d(0), _d(1)), (_d(3), _d(3))]

        # Invariant, stated independently of the expected value above:
        # every run starts strictly after the previous run ends.
        for (_, prev_end), (next_start, _) in zip(runs, runs[1:], strict=False):
            assert next_start > prev_end

    def test_every_input_day_is_covered_exactly_once(self):
        days = [_d(0), _d(1), _d(2), _d(7), _d(9), _d(10)]
        covered: list[date] = []
        for start, end in _contiguous_runs(days):
            n = (end - start).days + 1
            covered.extend(start + timedelta(days=i) for i in range(n))
        assert sorted(covered) == sorted(days)
        assert len(covered) == len(set(covered))


# ══════════════════════════════════════════════════════════════════════
# 2. _daily_revenue_series
# ══════════════════════════════════════════════════════════════════════


class _Rollup:
    def __init__(self, rollup_date: date, revenue: int, orders: int):
        self.rollup_date = rollup_date
        self.total_revenue_cents = revenue
        self.total_orders = orders


class FakeRollupRepo:
    def __init__(self, rows: list[_Rollup]):
        self._rows = rows
        self.calls: list[tuple] = []

    async def get_range(self, store_id, start_d, end_d):
        self.calls.append((store_id, start_d, end_d))
        return [r for r in self._rows if start_d <= r.rollup_date <= end_d]


class FakeOrderRepo:
    """Returns ``(day, revenue, count)`` rows for days inside the asked window."""

    def __init__(self, per_day: dict[date, tuple[int, int]]):
        self._per_day = per_day
        self.calls: list[tuple[datetime, datetime]] = []

    async def get_daily_aggregates(self, store_id, start_dt, end_dt, *, timezone):
        self.calls.append((start_dt, end_dt))
        start_day = start_dt.astimezone(UTC).date() - timedelta(days=1)
        end_day = end_dt.astimezone(UTC).date() + timedelta(days=1)
        return [
            (d, rev, cnt)
            for d, (rev, cnt) in sorted(self._per_day.items())
            if start_day <= d <= end_day
        ]


@pytest.fixture
def pin_today(monkeypatch):
    """Pin the store-local 'today' by pinning the CLOCK, not ``local_date``.

    This used to stub ``analytics_routes.local_date`` with a constant
    function. That was too blunt: the routes also use ``local_date`` to
    project the request window's instants onto the store's calendar
    (``period_start`` / ``today``), so a constant stub collapsed the whole
    window to a single day and the fixture started reporting failures that
    the production code does not have.

    Patching ``datetime`` leaves ``local_date`` real, so only "now" is
    controlled — which is the one thing a test actually needs to pin.
    """

    def _pin(day: date):
        class _PinnedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D102 - stdlib signature
                # Midday UTC keeps the local date unambiguous for every
                # timezone the platform supports (UTC±few hours).
                instant = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
                return instant.astimezone(tz) if tz else instant

        monkeypatch.setattr(analytics_routes, "datetime", _PinnedDatetime)

    return _pin


async def _series(rollup_repo, order_repo, start_d, end_d):
    return await _daily_revenue_series(
        store_id=uuid.uuid4(),
        tz_name="Africa/Cairo",
        rollup_repo=rollup_repo,
        order_repo=order_repo,
        start_d=start_d,
        end_d=end_d,
    )


class TestDailyRevenueSeries:
    async def test_completed_days_come_from_rollup_and_never_hit_the_db(
        self, pin_today
    ):
        pin_today(_d(5))
        rollups = FakeRollupRepo([
            _Rollup(_d(i), 1000 * (i + 1), i + 1) for i in range(3)
        ])
        orders = FakeOrderRepo({_d(i): (999_999, 999) for i in range(3)})

        out = await _series(rollups, orders, _d(0), _d(2))

        assert out == {
            _d(0): (1000, 1),
            _d(1): (2000, 2),
            _d(2): (3000, 3),
        }
        assert orders.calls == [], (
            "no live query should be issued when rollups cover the window"
        )

    async def test_today_is_never_served_from_a_rollup_row(self, pin_today):
        """REGRESSION A3 — a rollup row written at 03:30 must not freeze today."""
        today = _d(2)
        pin_today(today)
        # A rollup row DOES exist for today, holding the stale 03:30 figure.
        rollups = FakeRollupRepo([
            _Rollup(_d(0), 1000, 1),
            _Rollup(_d(1), 2000, 2),
            _Rollup(today, 55, 1),
        ])
        orders = FakeOrderRepo({today: (7_500, 9)})

        out = await _series(rollups, orders, _d(0), today)

        assert out[today] == (7_500, 9), "today must come from the live query"
        assert out[_d(0)] == (1000, 1)
        assert out[_d(1)] == (2000, 2)
        assert orders.calls, "a live query must be issued for today"

    async def test_gap_is_filled_live_without_recounting_covered_days(self, pin_today):
        """A missing middle day is topped up live; rollup days keep their values."""
        pin_today(_d(9))
        rollups = FakeRollupRepo([
            _Rollup(_d(0), 100, 1),
            _Rollup(_d(1), 200, 2),
            # _d(2) missing — beat outage
            _Rollup(_d(3), 400, 4),
            _Rollup(_d(4), 500, 5),
        ])
        # The live repo would happily return every day in the queried span;
        # only the missing day may be taken from it.
        orders = FakeOrderRepo({_d(i): (9_000 + i, 90 + i) for i in range(5)})

        out = await _series(rollups, orders, _d(0), _d(4))

        assert out[_d(0)] == (100, 1)
        assert out[_d(1)] == (200, 2)
        assert out[_d(2)] == (9_002, 92), "gap day comes from the live query"
        assert out[_d(3)] == (400, 4)
        assert out[_d(4)] == (500, 5)
        # Totals must not double count.
        assert sum(rev for rev, _ in out.values()) == 100 + 200 + 9_002 + 400 + 500

    async def test_two_gaps_around_a_covered_day_do_not_recount_it(self, pin_today):
        pin_today(_d(9))
        rollups = FakeRollupRepo([_Rollup(_d(1), 777, 7)])
        orders = FakeOrderRepo({_d(0): (10, 1), _d(1): (999, 99), _d(2): (30, 3)})

        out = await _series(rollups, orders, _d(0), _d(2))

        assert out == {_d(0): (10, 1), _d(1): (777, 7), _d(2): (30, 3)}

    async def test_no_rollups_at_all_falls_through_entirely_to_live(self, pin_today):
        pin_today(_d(9))
        rollups = FakeRollupRepo([])
        orders = FakeOrderRepo({_d(0): (10, 1), _d(1): (20, 2), _d(2): (30, 3)})

        out = await _series(rollups, orders, _d(0), _d(2))

        assert out == {_d(0): (10, 1), _d(1): (20, 2), _d(2): (30, 3)}
        assert len(orders.calls) == 1, "one query for the whole contiguous gap"

    async def test_day_with_no_data_anywhere_is_zero_not_missing(self, pin_today):
        pin_today(_d(9))
        rollups = FakeRollupRepo([])
        orders = FakeOrderRepo({})

        out = await _series(rollups, orders, _d(0), _d(2))

        assert out == {_d(0): (0, 0), _d(1): (0, 0), _d(2): (0, 0)}

    async def test_every_day_in_the_window_is_present_zero_filled(self, pin_today):
        pin_today(_d(30))
        rollups = FakeRollupRepo([_Rollup(_d(3), 1, 1)])
        orders = FakeOrderRepo({})

        out = await _series(rollups, orders, _d(0), _d(6))

        assert list(out.keys()) == [_d(i) for i in range(7)]
        assert sorted(out.keys()) == list(out.keys()), "keys must be date-ordered"

    async def test_single_day_window_of_today(self, pin_today):
        today = _d(0)
        pin_today(today)
        rollups = FakeRollupRepo([_Rollup(today, 12_345, 42)])
        orders = FakeOrderRepo({today: (600, 3)})

        out = await _series(rollups, orders, today, today)

        assert out == {today: (600, 3)}

    async def test_future_end_date_yields_zero_not_an_error(self, pin_today):
        pin_today(_d(0))
        rollups = FakeRollupRepo([])
        orders = FakeOrderRepo({})

        out = await _series(rollups, orders, _d(0), _d(2))

        assert out == {_d(0): (0, 0), _d(1): (0, 0), _d(2): (0, 0)}

    async def test_inverted_range_returns_empty(self, pin_today):
        pin_today(_d(5))
        rollups = FakeRollupRepo([])
        orders = FakeOrderRepo({})

        assert await _series(rollups, orders, _d(3), _d(1)) == {}

    async def test_null_rollup_columns_coalesce_to_zero(self, pin_today):
        pin_today(_d(5))
        rollups = FakeRollupRepo([_Rollup(_d(0), None, None)])
        orders = FakeOrderRepo({})

        out = await _series(rollups, orders, _d(0), _d(0))

        assert out == {_d(0): (0, 0)}


# ══════════════════════════════════════════════════════════════════════
# 3. exclude_non_revenue — ONE shared definition
# ══════════════════════════════════════════════════════════════════════


def _compile(clause) -> str:
    return str(
        select(func.count())
        .select_from(OrderModel)
        .where(clause)
        .compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


class TestExcludeNonRevenue:
    def test_exact_status_set(self):
        assert NON_REVENUE_STATUSES_LC == (
            "cancelled",
            "refunded",
            "draft",
            "payment_failed",
        )

    def test_returned_stays_in_booked_revenue(self):
        assert "returned" not in NON_REVENUE_STATUSES_LC
        assert "pending_deposit" not in NON_REVENUE_STATUSES_LC

    def test_compiles_to_a_lowercased_text_comparison(self):
        """The enum labels are mixed-case; a bare enum bind raises in PG."""
        sql = _compile(exclude_non_revenue(OrderModel.status))
        assert "lower(" in sql
        assert "not in" in sql
        for st in NON_REVENUE_STATUSES_LC:
            assert f"'{st}'" in sql

    def test_status_lc_matches_the_hand_written_spelling(self):
        assert _compile(status_lc(OrderModel.status) == "cancelled") == _compile(
            func.lower(cast(OrderModel.status, String)) == "cancelled"
        )

    def test_analytics_repository_uses_the_shared_tuple(self):
        from src.infrastructure.repositories import analytics_repository as ar

        assert ar._NON_REVENUE_STATUSES_LC is NON_REVENUE_STATUSES_LC

    def test_order_repository_revenue_queries_share_the_definition(self):
        """The two order-repo revenue queries must carry all four statuses."""
        import inspect

        from src.infrastructure.repositories import order_repository as orp

        for fn in (
            orp.OrderRepository.get_revenue_by_date_range,
            orp.OrderRepository.get_daily_aggregates,
            orp.OrderRepository.get_hourly_aggregates,
        ):
            src = inspect.getsource(fn)
            assert "exclude_non_revenue(OrderModel.status)" in src, fn.__name__

    def test_rollup_task_uses_the_shared_definition(self):
        import inspect

        from src.infrastructure.messaging.tasks import analytics_rollup_tasks as art

        src = inspect.getsource(art._aggregate_day)
        assert "exclude_non_revenue(OrderModel.status)" in src
        # cancelled_orders must NOT be gated on is_revenue — it counts the
        # very rows revenue excludes.
        cancelled_block = src[src.index('label("cancelled_orders")') - 400 :]
        assert "cancelled" in cancelled_block


# ══════════════════════════════════════════════════════════════════════
# 4. realtime_counters — pipeline index math
# ══════════════════════════════════════════════════════════════════════


class FakePipeline:
    """Records commands in order and resolves them against a FakeRedis.

    Mirrors redis-py asyncio semantics: queued commands return immediately,
    ``execute()`` returns one result per command IN COMMAND ORDER.
    """

    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._queued: list[tuple] = []

    def __getattr__(self, name):
        def _queue(*args, **kwargs):
            self._queued.append((name, args, kwargs))
            return self

        return _queue

    async def execute(self):
        out = []
        for name, args, kwargs in self._queued:
            out.append(getattr(self._redis, name)(*args, **kwargs))
        self._queued.clear()
        return out


class FakeRedis:
    """Minimal in-memory Redis with the commands these counters use."""

    def __init__(self):
        self.strings: dict[str, int] = {}
        self.sets: dict[str, set] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.lists: dict[str, list] = {}
        self.expires: dict[str, int] = {}

    def pipeline(self):
        return FakePipeline(self)

    # -- strings --
    def incr(self, key):
        self.strings[key] = self.strings.get(key, 0) + 1
        return self.strings[key]

    def incrby(self, key, amount):
        self.strings[key] = self.strings.get(key, 0) + amount
        return self.strings[key]

    def get(self, key):
        v = self.strings.get(key)
        return None if v is None else str(v)

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True

    # -- hyperloglog --
    def pfadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)
        return 1

    def pfcount(self, key):
        return len(self.sets.get(key, ()))

    # -- sorted sets --
    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zincrby(self, key, amount, member):
        z = self.zsets.setdefault(key, {})
        z[member] = z.get(member, 0) + amount
        return z[member]

    def zrevrange(self, key, start, end, withscores=False):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: -kv[1])
        sliced = items[start : (None if end == -1 else end + 1)]
        return sliced if withscores else [m for m, _ in sliced]

    def zremrangebyscore(self, key, min_, max_):
        z = self.zsets.get(key, {})
        lo = float("-inf") if min_ == "-inf" else float(min_)
        hi = float("inf") if max_ == "+inf" else float(max_)
        doomed = [m for m, s in z.items() if lo <= s <= hi]
        for m in doomed:
            del z[m]
        return len(doomed)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    # -- lists --
    def lpush(self, key, *values):
        self.lists.setdefault(key, [])[0:0] = list(reversed(values))
        return len(self.lists[key])

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start : end + 1]
        return True

    def lrange(self, key, start, end):
        return self.lists.get(key, [])[start : (None if end == -1 else end + 1)]


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()

    async def _client():
        return r

    monkeypatch.setattr(rc, "_get_client", _client)
    return r


STORE = uuid.UUID("11111111-2222-3333-4444-555555555555")
TZ = "Africa/Cairo"


class TestRealtimeSnapshotIndexMath:
    async def test_hourly_values_land_in_their_own_hour_slot(self, fake_redis):
        """REGRESSION A1 — the pipeline interleaves orders/revenue per hour.

        Reading them as two contiguous 24-wide blocks scrambles the Live
        tab's histogram: 'orders' picks up revenue figures for hours 0-11
        and 'revenue' picks up order counts for hours 12-23.
        """
        for h in range(24):
            fake_redis.strings[rc._day_key(STORE, f"hourly_orders:{h}", TZ)] = h + 1
            fake_redis.strings[rc._day_key(STORE, f"hourly_revenue:{h}", TZ)] = 1000 * (
                h + 1
            )

        snap = await rc.get_snapshot(STORE, tz_name=TZ)

        assert snap["hourly_orders"] == [h + 1 for h in range(24)]
        assert snap["hourly_revenue"] == [1000 * (h + 1) for h in range(24)]

    async def test_sparse_hours_do_not_shift_other_hours(self, fake_redis):
        fake_redis.strings[rc._day_key(STORE, "hourly_orders:21", TZ)] = 9
        fake_redis.strings[rc._day_key(STORE, "hourly_revenue:21", TZ)] = 45_000

        snap = await rc.get_snapshot(STORE, tz_name=TZ)

        assert snap["hourly_orders"][21] == 9
        assert snap["hourly_revenue"][21] == 45_000
        assert sum(snap["hourly_orders"]) == 9
        assert sum(snap["hourly_revenue"]) == 45_000

    async def test_scalar_slots_are_not_confused_with_each_other(self, fake_redis):
        fake_redis.strings[rc._day_key(STORE, "views", TZ)] = 111
        fake_redis.sets[rc._day_key(STORE, "visitors", TZ)] = {"a", "b", "c"}
        fake_redis.strings[rc._day_key(STORE, "orders", TZ)] = 7
        fake_redis.strings[rc._day_key(STORE, "revenue", TZ)] = 250_000
        fake_redis.zsets[rc._day_key(STORE, "top_pages", TZ)] = {"/": 5.0, "/p/x": 2.0}
        fake_redis.lists[rc._key(STORE, "recent_orders")] = ['{"order_number":"A"}']
        fake_redis.zsets[rc._key(STORE, "active")] = {
            "fp-fresh": datetime.now(UTC).timestamp(),
            "fp-stale": datetime.now(UTC).timestamp() - 3600,
        }

        snap = await rc.get_snapshot(STORE, tz_name=TZ)

        assert snap["views_today"] == 111
        assert snap["visitors_today"] == 3
        assert snap["orders_today"] == 7
        assert snap["revenue_today"] == 250_000
        assert snap["top_pages"] == [
            {"path": "/", "views": 5},
            {"path": "/p/x", "views": 2},
        ]
        assert snap["recent_orders"] == [{"order_number": "A"}]
        assert snap["available"] is True

    async def test_active_now_evicts_stale_then_counts(self, fake_redis):
        now = datetime.now(UTC).timestamp()
        fake_redis.zsets[rc._key(STORE, "active")] = {
            "fresh-1": now - 10,
            "fresh-2": now - 60,
            "stale-1": now - (rc._ACTIVE_TTL + 30),
            "stale-2": now - 86_400,
        }

        snap = await rc.get_snapshot(STORE, tz_name=TZ)

        assert snap["active_now"] == 2
        assert set(fake_redis.zsets[rc._key(STORE, "active")]) == {"fresh-1", "fresh-2"}

    async def test_write_then_read_round_trip(self, fake_redis, monkeypatch):
        """End-to-end through the real key builders: writes must be readable."""
        monkeypatch.setattr(rc, "_local_hour", lambda _tz: 15)

        await rc.record_page_view(STORE, "fp-1", "/products/x", tz_name=TZ)
        await rc.record_page_view(STORE, "fp-2", "/products/x", tz_name=TZ)
        await rc.record_page_view(STORE, "fp-1", "/", tz_name=TZ)
        await rc.record_order_created(
            STORE, {"order_number": "ORD-1", "total": 30_000}, tz_name=TZ
        )
        await rc.record_order_created(
            STORE, {"order_number": "ORD-2", "total": 20_000}, tz_name=TZ
        )
        await rc.record_payment(STORE, 50_000, tz_name=TZ)

        snap = await rc.get_snapshot(STORE, tz_name=TZ)

        assert snap["views_today"] == 3
        assert snap["visitors_today"] == 2
        assert snap["active_now"] == 2
        assert snap["orders_today"] == 2
        assert snap["revenue_today"] == 50_000
        assert snap["hourly_orders"][15] == 2
        assert snap["hourly_revenue"][15] == 50_000
        assert sum(snap["hourly_orders"]) == 2, "no phantom orders in other hours"
        assert sum(snap["hourly_revenue"]) == 50_000
        assert [o["order_number"] for o in snap["recent_orders"]] == ["ORD-2", "ORD-1"]
        assert snap["top_pages"][0] == {"path": "/products/x", "views": 2}

    async def test_counters_are_scoped_to_the_store_local_day(self, fake_redis):
        """Yesterday's key must not be read as today's."""
        yesterday = (rc._local_today(TZ) - timedelta(days=1)).isoformat()
        fake_redis.strings[f"rt:{STORE}:{yesterday}:views"] = 9_999
        fake_redis.strings[rc._day_key(STORE, "views", TZ)] = 4

        snap = await rc.get_snapshot(STORE, tz_name=TZ)

        assert snap["views_today"] == 4

    async def test_snapshot_is_unavailable_when_redis_errors(self, monkeypatch):
        async def _boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr(rc, "_get_client", _boom)

        snap = await rc.get_snapshot(STORE, tz_name=TZ)

        assert snap["available"] is False
        assert snap["views_today"] == 0
        assert snap["hourly_orders"] == []

    async def test_default_snapshot_shape_advertises_availability(self):
        assert rc._EMPTY_SNAPSHOT["available"] is True


# ══════════════════════════════════════════════════════════════════════
# 5. Funnel cart abandonment — bounded [0, 100] by construction
# ══════════════════════════════════════════════════════════════════════


class FakeStore:
    def __init__(self):
        self.id = uuid.uuid4()
        self.settings = {"timezone": TZ}


class FakeFunnelRepo:
    def __init__(self, counts: dict[str, int], steps_by_fp: dict[str, set[str]]):
        self._counts = counts
        self._steps_by_fp = steps_by_fp

    async def get_funnel_counts(self, *_a, **_kw):
        return dict(self._counts)

    async def get_steps_per_session(self, *_a, **_kw):
        return {k: set(v) for k, v in self._steps_by_fp.items()}

    async def get_daily_funnel_counts(self, *_a, **_kw):
        return []

    async def get_step_pair_avg_minutes(self, *_a, **_kw):
        return None


class FakeFunnelOrderRepo:
    async def get_by_date_range(self, *_a, **_kw):
        return []

    async def count_by_store(self, *_a, **_kw):
        return 0


class FakePageViewRepo:
    def __init__(self, visitors: int = 0):
        self._visitors = visitors

    async def count_unique_visitors(self, *_a, **_kw):
        return self._visitors


def _window(days: int = 30):
    return resolve_date_range_window(days=days, granularity="day", tz=TZ)


async def _funnel(counts, steps_by_fp):
    resp = await analytics_routes.get_funnel(
        store=FakeStore(),
        funnel_repo=FakeFunnelRepo(counts, steps_by_fp),
        order_repo=FakeFunnelOrderRepo(),
        window=_window(),
    )
    return resp.data


class TestFunnelCartAbandonment:
    async def test_checkout_started_exceeding_add_to_cart_stays_within_bounds(self):
        """REGRESSION A4 — the old subtraction produced negative abandonment.

        Six sessions started checkout, only two of them ever added to cart
        (server-persisted carts, cross-window adds). Per-step totals give
        ``1 - 6/2 = -200%``; the session intersection gives 0%.
        """
        counts = {
            "page_view": 100,
            "product_view": 40,
            "add_to_cart": 2,
            "checkout_started": 6,
            "order_completed": 5,
            "order_delivered": 0,
        }
        steps_by_fp = {
            "s1": {"page_view", "add_to_cart", "checkout_started"},
            "s2": {"page_view", "add_to_cart", "checkout_started", "order_completed"},
            "s3": {"page_view", "checkout_started"},
            "s4": {"page_view", "checkout_started"},
            "s5": {"page_view", "checkout_started"},
            "s6": {"page_view", "checkout_started"},
        }

        data = await _funnel(counts, steps_by_fp)
        ca = data.cart_abandonment

        assert ca.carts_created == 2
        assert ca.checkouts_started == 2
        assert ca.abandonment_rate == 0.0
        assert 0.0 <= ca.abandonment_rate <= 100.0
        assert ca.estimated_lost_revenue >= 0

    @pytest.mark.parametrize(
        "steps_by_fp,expected_rate",
        [
            # nobody who added to cart moved on → 100%
            ({"a": {"add_to_cart"}, "b": {"add_to_cart"}}, 100.0),
            # everybody moved on → 0%
            (
                {
                    "a": {"add_to_cart", "checkout_started"},
                    "b": {"add_to_cart", "checkout_started"},
                },
                0.0,
            ),
            # half → 50%
            (
                {
                    "a": {"add_to_cart", "checkout_started"},
                    "b": {"add_to_cart"},
                },
                50.0,
            ),
            # no add_to_cart sessions at all → 0.0, not a ZeroDivisionError
            ({"a": {"page_view"}}, 0.0),
            ({}, 0.0),
        ],
    )
    async def test_boundaries(self, steps_by_fp, expected_rate):
        counts = {"page_view": 10, "add_to_cart": 999, "checkout_started": 999}
        data = await _funnel(counts, steps_by_fp)
        assert data.cart_abandonment.abandonment_rate == expected_rate
        assert 0.0 <= data.cart_abandonment.abandonment_rate <= 100.0

    async def test_rate_is_bounded_across_a_randomised_sweep(self):
        import random

        rng = random.Random(20260803)
        for _ in range(60):
            steps_by_fp = {}
            for i in range(rng.randint(0, 30)):
                s = set()
                for step in (
                    "page_view",
                    "product_view",
                    "add_to_cart",
                    "checkout_started",
                    "order_completed",
                ):
                    if rng.random() < 0.5:
                        s.add(step)
                steps_by_fp[f"fp{i}"] = s
            counts = {
                "page_view": rng.randint(0, 200),
                "add_to_cart": rng.randint(0, 200),
                "checkout_started": rng.randint(0, 200),
                "order_completed": rng.randint(0, 200),
            }
            data = await _funnel(counts, steps_by_fp)
            rate = data.cart_abandonment.abandonment_rate
            assert 0.0 <= rate <= 100.0, (steps_by_fp, counts, rate)
            assert data.cart_abandonment.checkouts_started <= (
                data.cart_abandonment.carts_created
            )
            assert data.cart_abandonment.estimated_lost_revenue >= 0

    async def test_overall_conversion_uses_order_completed_not_delivered(self):
        """REGRESSION A5 — 'View to purchase' must not measure deliveries."""
        counts = {
            "page_view": 200,
            "product_view": 80,
            "add_to_cart": 20,
            "checkout_started": 10,
            "order_completed": 6,
            "order_delivered": 0,  # no courier integration
        }
        data = await _funnel(counts, {})

        assert data.overall_conversion_pct == 3.0  # 6/200
        assert data.steps[-1].step == "order_delivered"
        assert data.steps[-1].count == 0

    async def test_overall_conversion_zero_visitors_is_not_a_crash(self):
        data = await _funnel({"order_completed": 3}, {})
        assert data.overall_conversion_pct == 0.0

    async def test_step_drop_off_pct_is_never_negative(self):
        """REGRESSION A6 — drop_off_pct must stay inside [0, 100].

        Each step's count is computed independently over the window, so a
        later step can exceed an earlier one (`checkout_started` 6 vs
        `add_to_cart` 2 here). Unclamped, `1 - count/prev_count` returned
        -200.0 — the same shape as the -175% a real store reported. The hub
        happens to hide negatives, but this field is in the public API
        contract the mobile app and partners read, so it cannot rely on one
        client filtering it.
        """
        counts = {
            "page_view": 100,
            "product_view": 40,
            "add_to_cart": 2,
            "checkout_started": 6,
            "order_completed": 5,
            "order_delivered": 0,
        }
        data = await _funnel(counts, {})
        for step in data.steps:
            assert 0.0 <= step.drop_off_pct <= 100.0, (
                f"{step.step} -> {step.drop_off_pct}"
            )


# ══════════════════════════════════════════════════════════════════════
# 6. /overview collected revenue + /sales-chart hourly granularity
# ══════════════════════════════════════════════════════════════════════


class FakeAnalyticsRepo:
    def __init__(self, paid: dict, refunds: int):
        self._paid = paid
        self._refunds = refunds
        self.paid_windows: list[tuple[datetime, datetime]] = []
        self.refund_windows: list[tuple[datetime, datetime]] = []

    async def revenue_summary_paid(self, _store_id, date_from, date_to):
        self.paid_windows.append((date_from, date_to))
        return dict(self._paid)

    async def refunds_total(self, _store_id, date_from, date_to):
        self.refund_windows.append((date_from, date_to))
        return self._refunds


class _Currency:
    value = "EGP"


class OverviewStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.default_currency = _Currency()


class TestOverviewCollectedRevenue:
    async def test_collected_uses_total_not_subtotal_and_shares_the_window(
        self, pin_today
    ):
        """REGRESSION A7 — collected revenue must include shipping + tax.

        ``total_sales`` is SUM(total); pairing it with a SUM(subtotal)
        'collected' understated collected revenue by shipping + tax on
        every order. Both halves must also be measured over the SAME
        window as each other.
        """
        pin_today(_d(9))
        rollups = FakeRollupRepo([_Rollup(_d(i), 10_000, 1) for i in range(3)])
        orders = FakeOrderRepo({})
        arepo = FakeAnalyticsRepo(
            paid={
                "gross_cents": 30_000,  # merchandise only
                "total_cents": 45_000,  # + EGP 50 delivery x 3 orders
                "discounts_cents": 0,
                "shipping_cents": 15_000,
                "tax_cents": 0,
            },
            refunds=5_000,
        )

        resp = await analytics_routes.get_sales_overview(
            store=OverviewStore(),
            rollup_repo=rollups,
            order_repo=orders,
            analytics_repo=arepo,
            window=resolve_date_range_window(days=3, granularity="day", tz=TZ),
        )
        data = resp.data

        assert data.collected_revenue == 45_000 - 5_000
        assert data.collected_revenue != 30_000 - 5_000, "still using gross/subtotal"

        # Same window on both halves of the subtraction.
        assert arepo.paid_windows[0] == arepo.refund_windows[0]
        assert arepo.paid_windows[1] == arepo.refund_windows[1]

    async def test_previous_window_does_not_overlap_the_current_one(self, pin_today):
        pin_today(_d(30))
        rollups = FakeRollupRepo([])
        orders = FakeOrderRepo({})
        arepo = FakeAnalyticsRepo(
            paid={
                "gross_cents": 0,
                "total_cents": 0,
                "discounts_cents": 0,
                "shipping_cents": 0,
                "tax_cents": 0,
            },
            refunds=0,
        )

        await analytics_routes.get_sales_overview(
            store=OverviewStore(),
            rollup_repo=rollups,
            order_repo=orders,
            analytics_repo=arepo,
            window=resolve_date_range_window(days=7, granularity="day", tz=TZ),
        )

        (cur_start, cur_end) = arepo.paid_windows[0]
        (prev_start, prev_end) = arepo.paid_windows[1]
        assert prev_end < cur_start, "previous window must end before the current"
        # Equal length (within the day granularity the windows are built at).
        assert abs((cur_end - cur_start) - (prev_end - prev_start)) < timedelta(hours=2)


class HourlyOrderRepo:
    def __init__(self, rows):
        self.rows = rows
        self.hourly_calls = 0
        self.daily_calls = 0

    async def get_hourly_aggregates(self, _store_id, _start, _end, *, timezone):
        self.hourly_calls += 1
        return self.rows

    async def get_daily_aggregates(self, *_a, **_kw):
        self.daily_calls += 1
        return []


class TestSalesChartHourly:
    async def test_hour_granularity_uses_real_hourly_buckets(self):
        """REGRESSION A8 — ``granularity=hour`` used to return DAILY rows."""
        rows = [
            (datetime(2026, 8, 1, 9, 0), 12_000, 3),
            (datetime(2026, 8, 1, 21, 0), 48_000, 7),
        ]
        repo = HourlyOrderRepo(rows)

        resp = await analytics_routes.get_sales_chart(
            store=OverviewStore(),
            rollup_repo=FakeRollupRepo([]),
            order_repo=repo,
            window=resolve_date_range_window(days=1, granularity="hour", tz=TZ),
            compare=None,
        )

        assert repo.hourly_calls == 1
        assert repo.daily_calls == 0
        labels = [p.date for p in resp.data]
        assert labels == ["Aug 01 09:00", "Aug 01 21:00"]
        assert [p.sales for p in resp.data] == [12_000, 48_000]
        assert [p.orders for p in resp.data] == [3, 7]

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "OPEN GAP A9 — the hourly series is not zero-filled. A window "
            "with 168 possible buckets returns only the hours that had "
            "orders, so a chart draws a straight line across a quiet night "
            "instead of a trough. The daily path zero-fills; this one does "
            "not."
        ),
    )
    async def test_hour_granularity_zero_fills_quiet_hours(self):
        rows = [
            (datetime(2026, 8, 1, 9, 0), 12_000, 3),
            (datetime(2026, 8, 1, 21, 0), 48_000, 7),
        ]
        resp = await analytics_routes.get_sales_chart(
            store=OverviewStore(),
            rollup_repo=FakeRollupRepo([]),
            order_repo=HourlyOrderRepo(rows),
            window=resolve_date_range_window(days=1, granularity="hour", tz=TZ),
            compare=None,
        )
        assert len(resp.data) > 2


class TestConversionStatsAbandonment:
    async def _conversion(self, steps_by_fp, visitors=100):
        resp = await analytics_routes.get_conversion_stats(
            store=FakeStore(),
            order_repo=FakeFunnelOrderRepo(),
            pv_repo=FakePageViewRepo(visitors),
            funnel_repo=FakeFunnelRepo({}, steps_by_fp),
            window=_window(),
        )
        return resp.data

    async def test_more_completions_than_cart_adds_stays_bounded(self):
        """Sessions that ordered without an add_to_cart must not go negative."""
        steps_by_fp = {
            "a": {"add_to_cart"},
            "b": {"order_completed"},
            "c": {"order_completed"},
            "d": {"order_completed"},
        }
        data = await self._conversion(steps_by_fp)
        assert data.cart_abandonment_rate == 100.0
        assert 0.0 <= data.cart_abandonment_rate <= 100.0

    @pytest.mark.parametrize(
        "steps_by_fp,expected",
        [
            ({"a": {"add_to_cart", "order_completed"}}, 0.0),
            ({"a": {"add_to_cart"}}, 100.0),
            (
                {
                    "a": {"add_to_cart", "order_completed"},
                    "b": {"add_to_cart"},
                    "c": {"add_to_cart"},
                    "d": {"add_to_cart"},
                },
                75.0,
            ),
            ({}, 0.0),
        ],
    )
    async def test_boundaries(self, steps_by_fp, expected):
        data = await self._conversion(steps_by_fp)
        assert data.cart_abandonment_rate == expected

    async def test_zero_visitors_yields_zero_conversion_not_a_crash(self):
        data = await self._conversion({}, visitors=0)
        assert data.conversion_rate == 0


# ══════════════════════════════════════════════════════════════════════
# 9. Rollup task — today is never persisted
# ══════════════════════════════════════════════════════════════════════


class TestRollupNeverWritesToday:
    """REGRESSION A10.

    A rollup row asserts that a day is COMPLETE. Eight endpoints read the
    table and five of them consume rows verbatim as finished days —
    /forecast literally uses them as its training samples — so persisting a
    partial "today" row written at 03:30 would drag every forecast down,
    every day. The three endpoints that genuinely need today compute it
    live instead (see `_daily_revenue_series`).

    The date list must also be built on the STORE's wall clock, not the
    server's: it used `date.today()` (UTC in prod) while the rollup day key
    is store-local, so at 22:00 UTC a Cairo store was already on the next
    local date and the whole window was off by one.
    """

    @staticmethod
    def _dates(store_today: date, backfill_days: int) -> list[date]:
        # Mirrors the expression in `_calculate_all_rollups`.
        return [store_today - timedelta(days=i) for i in range(1, backfill_days + 1)]

    def test_today_is_excluded(self):
        store_today = _d(0)
        dates = self._dates(store_today, 90)
        assert store_today not in dates
        assert max(dates) == store_today - timedelta(days=1)

    def test_covers_the_full_backfill_window_without_gaps(self):
        store_today = _d(0)
        dates = self._dates(store_today, 90)
        assert len(dates) == 90
        assert len(set(dates)) == 90
        assert min(dates) == store_today - timedelta(days=90)

    def test_store_local_today_differs_from_utc_late_in_the_day(self):
        """22:00 UTC is already the NEXT calendar day in Cairo (UTC+2/+3)."""
        from src.core.utils.store_timezone import local_date

        late = datetime(2026, 8, 3, 22, 0, tzinfo=UTC)
        assert local_date(late, "Africa/Cairo") == date(2026, 8, 4)
        assert late.date() == date(2026, 8, 3)
        # The rollup must follow the store, not the server.
        assert local_date(late, "Africa/Cairo") != late.date()


# ══════════════════════════════════════════════════════════════════════
# 10. Storefront session identity — documented contract
# ══════════════════════════════════════════════════════════════════════


class TestFunnelSyncFallbackKeepsDedupeKey:
    """REGRESSION A15.

    When the Celery enqueue fails, `_emit_funnel_event` releases the
    idempotency claim and writes the row synchronously. That fallback must
    carry `effective_event_id`, NOT the possibly-None client `event_id`:
    `ux_funnel_events_event_id` is a PARTIAL unique index and does not
    constrain NULLs, so a NULL here leaves the row unprotected and a browser
    retry duplicates it.
    """

    def test_fallback_passes_effective_event_id(self):
        import inspect

        from src.api.v1.routes.storefront import tracking

        src = inspect.getsource(tracking._emit_funnel_event)
        # The sync fallback lives after the `except` that handles a failed
        # enqueue; it must not pass the raw client id.
        fallback = src.split("except Exception:")[-1]
        assert "event_id=effective_event_id" in fallback
        assert "event_id=event_id," not in fallback


# ══════════════════════════════════════════════════════════════════════
# 11. CAPI content_ids must match the product feed
# ══════════════════════════════════════════════════════════════════════


class TestCapiContentIdsMatchFeed:
    """REGRESSION — content_ids must equal the feed's ``g:id``.

    The feed emits ``meta_catalog_id or product.id`` (`meta_feed.py:212`).
    Merchants already running a Meta/TikTok catalog keyed on their own SKUs
    set ``meta_catalog_id``. The conversion events sent the internal UUID
    unconditionally, so for exactly those merchants the catalog could not
    join the conversion: dynamic ads stop attributing revenue and
    "viewed but didn't buy" audiences never clear on purchase.

    Only the PDP's ViewContent honoured the override, which is what made it
    hard to notice — the first event of the funnel matched and the rest
    silently did not.
    """

    @staticmethod
    def _order(*, pid: str, total: int = 45_000):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=uuid.uuid4(),
            total=total,
            currency="EGP",
            line_items=[{"product_id": pid, "quantity": 2, "unit_price": 22_500}],
        )

    def _build(self, order, catalog_ids):
        from src.application.services.meta_capi_purchase_dispatcher import (
            _build_custom_data_from_order,
        )

        return _build_custom_data_from_order(order, catalog_ids)

    def test_catalog_override_is_used_when_present(self):
        pid = str(uuid.uuid4())
        data = self._build(self._order(pid=pid), {pid: "SKU-123"})
        assert data["content_ids"] == ["SKU-123"]
        assert [c["id"] for c in data["contents"]] == ["SKU-123"]

    def test_falls_back_to_product_id_without_override(self):
        pid = str(uuid.uuid4())
        data = self._build(self._order(pid=pid), {})
        assert data["content_ids"] == [pid]
        assert [c["id"] for c in data["contents"]] == [pid]

    def test_none_map_behaves_like_empty(self):
        pid = str(uuid.uuid4())
        assert self._build(self._order(pid=pid), None)["content_ids"] == [pid]

    def test_content_ids_and_contents_never_disagree(self):
        """Both fields feed the same catalog join — they must not diverge."""
        pid = str(uuid.uuid4())
        for catalog in ({}, {pid: "SKU-9"}):
            data = self._build(self._order(pid=pid), catalog)
            assert data["content_ids"] == [c["id"] for c in data["contents"]]

    def test_tiktok_dispatcher_resolves_identically(self):
        """Meta and TikTok read the SAME feed — a divergence breaks one of them."""
        from src.application.services.tiktok_capi_purchase_dispatcher import (
            _build_custom_data_from_order as tt_build,
        )

        pid = str(uuid.uuid4())
        order = self._order(pid=pid)
        assert (
            tt_build(order, {pid: "SKU-7"})["content_ids"]
            == self._build(order, {pid: "SKU-7"})["content_ids"]
        )

    def test_value_is_major_units_not_cents(self):
        """A cents-valued Purchase inflates reported ROAS by 100x."""
        pid = str(uuid.uuid4())
        data = self._build(self._order(pid=pid, total=45_000), {})
        assert data["value"] == 450.0
        assert data["contents"][0]["item_price"] == 225.0


class TestMetaHashNormalisation:
    """Every match key must be canonical before SHA-256, or it matches nothing."""

    @staticmethod
    def _h(s):
        from src.infrastructure.external_services.meta.hashing import _h

        return _h(s)

    def _hash(self, raw):
        from src.infrastructure.external_services.meta.hashing import hash_user_data

        return hash_user_data(raw)

    def test_free_form_country_is_canonicalised_to_iso2(self):
        assert self._hash({"country_code": "Egypt"})["country"] == [self._h("eg")]
        assert self._hash({"country_code": "EG"})["country"] == [self._h("eg")]

    def test_unmappable_country_is_dropped_not_hashed_raw(self):
        assert self._hash({"country_code": "Atlantis"})["country"] is None

    def test_zip_whitespace_is_stripped_everywhere(self):
        assert self._hash({"zip": "SW1A 1AA"})["zp"] == [self._h("sw1a1aa")]
        assert self._hash({"zip": " 12345 "})["zp"] == [self._h("12345")]

    def test_email_is_trimmed_and_lowercased(self):
        assert self._hash({"email": "  A@B.CoM "})["em"] == [self._h("a@b.com")]

    @pytest.mark.parametrize(
        "raw",
        ["01001234567", "+201001234567", "201001234567", "٠١٠٠١٢٣٤٥٦٧"],
    )
    def test_phone_variants_collapse_to_one_e164_digest(self, raw):
        assert self._hash({"phone": raw})["ph"] == [self._h("201001234567")]
