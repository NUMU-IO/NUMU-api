"""Unit tests for the LTV-by-channel route helpers.

The SQL aggregation in
``AnalyticsRepository.ltv_by_channel`` is exercised by integration
tests against a live Postgres; this file covers the pure-Python
helpers that derive the displayed metrics from the raw repo rows.

The maths is small but easy to get wrong: AOV uses integer-cent
truncation (rounding fractional cents would mislead an EGP dashboard),
``orders_per_customer`` is a float ratio, and the weighted-average
LTV in the totals row must use ``Σrevenue / Σcustomers``, not the
unweighted mean of per-channel LTVs.
"""

from __future__ import annotations

import pytest

from src.api.v1.routes.stores.analytics import (
    LtvByChannelTotals,
    LtvChannelRow,
    _build_ltv_channel_row,
    _build_ltv_totals,
)


class TestBuildLtvChannelRow:
    def test_typical_cohort(self):
        row = _build_ltv_channel_row(
            channel="facebook",
            customer_count=10,
            total_orders=25,
            total_revenue_cents=500_000,  # 5,000 EGP
        )
        assert row.channel == "facebook"
        assert row.customer_count == 10
        assert row.total_orders == 25
        assert row.total_revenue_cents == 500_000
        # 500_000 / 25 = 20_000 cents
        assert row.average_order_value_cents == 20_000
        # 25 / 10 = 2.5
        assert row.orders_per_customer == 2.5
        # 500_000 / 10 = 50_000 cents
        assert row.ltv_cents == 50_000

    def test_aov_truncates_fractional_cents(self):
        # 100 / 3 = 33.33... — should truncate to 33
        row = _build_ltv_channel_row(
            channel="instagram",
            customer_count=1,
            total_orders=3,
            total_revenue_cents=100,
        )
        assert row.average_order_value_cents == 33

    def test_ltv_truncates_fractional_cents(self):
        # 100 / 3 = 33.33... — truncated
        row = _build_ltv_channel_row(
            channel="instagram",
            customer_count=3,
            total_orders=3,
            total_revenue_cents=100,
        )
        assert row.ltv_cents == 33

    def test_orders_per_customer_rounded_to_two_decimals(self):
        # 7 / 3 = 2.333... — rounded to 2.33
        row = _build_ltv_channel_row(
            channel="tiktok",
            customer_count=3,
            total_orders=7,
            total_revenue_cents=1_000,
        )
        assert row.orders_per_customer == 2.33

    def test_zero_customers_no_division_error(self):
        # Shouldn't happen from real repo data (the GROUP BY only emits
        # rows for present cohorts) but guard the arithmetic anyway.
        row = _build_ltv_channel_row(
            channel="direct",
            customer_count=0,
            total_orders=0,
            total_revenue_cents=0,
        )
        assert row.average_order_value_cents == 0
        assert row.orders_per_customer == 0.0
        assert row.ltv_cents == 0

    def test_zero_orders_with_nonzero_customers(self):
        # Cohort acquired but never bought (cancelled-only history).
        # LEFT JOIN means we still see the row; metrics must be 0.
        row = _build_ltv_channel_row(
            channel="email",
            customer_count=5,
            total_orders=0,
            total_revenue_cents=0,
        )
        assert row.average_order_value_cents == 0
        assert row.orders_per_customer == 0.0
        assert row.ltv_cents == 0
        assert row.customer_count == 5  # the cohort itself is real

    def test_direct_bucket_preserved(self):
        # `coalesce(..., 'direct')` in the repo means missing first-touch
        # data shows up under this label — the route shouldn't rename it.
        row = _build_ltv_channel_row(
            channel="direct",
            customer_count=12,
            total_orders=20,
            total_revenue_cents=400_000,
        )
        assert row.channel == "direct"


class TestBuildLtvTotals:
    def test_sums_across_channels(self):
        rows = [
            _build_ltv_channel_row("facebook", 10, 25, 500_000),
            _build_ltv_channel_row("instagram", 5, 8, 160_000),
            _build_ltv_channel_row("direct", 20, 30, 600_000),
        ]
        totals = _build_ltv_totals(rows)
        assert totals.customer_count == 35
        assert totals.total_orders == 63
        assert totals.total_revenue_cents == 1_260_000
        # Weighted: 1_260_000 / 35 = 36_000 cents
        assert totals.average_ltv_cents == 36_000

    def test_weighted_average_not_arithmetic_mean(self):
        """A small high-LTV cohort must not inflate the total LTV.

        Facebook: 100 customers, 1,000,000c revenue → 10,000c LTV
        VIP-channel: 1 customer, 100,000c revenue → 100,000c LTV

        Unweighted mean of per-channel LTVs would be 55,000c (wrong:
        99% of customers had a 10,000c LTV). Weighted average is
        1,100,000 / 101 ≈ 10,891c.
        """
        rows = [
            _build_ltv_channel_row("facebook", 100, 100, 1_000_000),
            _build_ltv_channel_row("vip", 1, 1, 100_000),
        ]
        totals = _build_ltv_totals(rows)
        assert totals.customer_count == 101
        assert totals.average_ltv_cents == 1_100_000 // 101  # 10_891

    def test_empty_list_returns_zero_totals(self):
        totals = _build_ltv_totals([])
        assert totals.customer_count == 0
        assert totals.total_orders == 0
        assert totals.total_revenue_cents == 0
        # No division by zero — empty cohort = zero average.
        assert totals.average_ltv_cents == 0

    def test_all_zero_revenue_rows(self):
        # Cohorts exist but nobody bought — sums are 0, average is 0.
        rows = [
            _build_ltv_channel_row("source-a", 3, 0, 0),
            _build_ltv_channel_row("source-b", 5, 0, 0),
        ]
        totals = _build_ltv_totals(rows)
        assert totals.customer_count == 8
        assert totals.total_orders == 0
        assert totals.total_revenue_cents == 0
        assert totals.average_ltv_cents == 0

    def test_returns_pydantic_model(self):
        # The route returns this via Pydantic; if the helper returned a
        # bare dict the route's response_model would silently coerce or
        # validate-fail. Confirm we're handing back a real model.
        rows = [_build_ltv_channel_row("source-a", 1, 1, 1_000)]
        totals = _build_ltv_totals(rows)
        assert isinstance(totals, LtvByChannelTotals)


class TestLtvRowPydanticShape:
    def test_required_fields_present(self):
        row = _build_ltv_channel_row("source-a", 1, 1, 1_000)
        # Spot-check the response shape — the merchant hub treats the
        # field names as a contract.
        as_dict = row.model_dump()
        assert set(as_dict.keys()) == {
            "channel",
            "customer_count",
            "total_orders",
            "total_revenue_cents",
            "average_order_value_cents",
            "orders_per_customer",
            "ltv_cents",
        }

    def test_returns_pydantic_model(self):
        row = _build_ltv_channel_row("source-a", 1, 1, 1_000)
        assert isinstance(row, LtvChannelRow)


class TestRepositoryGroupByValidation:
    """The repo method itself raises on invalid ``group_by``."""

    @pytest.mark.parametrize("bad", ["", "SOURCE", "domain", "  source  ", "src"])
    def test_repo_rejects_invalid_group_by(self, bad: str):
        # Pure ValueError raised by the validator — no DB session
        # touched, so we can test without a fixture.
        from src.infrastructure.repositories.analytics_repository import (
            AnalyticsRepository,
        )

        # The validator runs synchronously before any await, so we
        # don't need to invoke through asyncio. Call via the bound
        # method on a None session — the line that raises is the very
        # first statement in the body.
        repo = AnalyticsRepository.__new__(AnalyticsRepository)
        repo.session = None  # type: ignore[assignment]

        import asyncio

        with pytest.raises(ValueError, match="group_by="):
            asyncio.run(
                repo.ltv_by_channel(
                    store_id=None,  # type: ignore[arg-type]
                    date_from=None,  # type: ignore[arg-type]
                    date_to=None,  # type: ignore[arg-type]
                    group_by=bad,
                )
            )

    @pytest.mark.parametrize("good", ["source", "medium", "campaign"])
    def test_repo_accepts_valid_group_by_keys(self, good: str):
        from src.infrastructure.repositories.analytics_repository import (
            AnalyticsRepository,
        )

        # The mapping must contain exactly the route's allowed values
        # — the route owns the public contract, the repo owns the
        # JSON field translation. Keep them in sync.
        assert good in AnalyticsRepository._LTV_GROUP_FIELDS
        assert AnalyticsRepository._LTV_GROUP_FIELDS[good].startswith("utm_")
