"""Executive dashboard gauges (AI-6) — pure-math unit tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.application.services.executive_service import (
    briefing,
    customer_gauge,
    inventory_gauge,
    marketing_gauge,
    profit_gauge,
    revenue_gauge,
)

CAIRO = ZoneInfo("Africa/Cairo")


def _weeks(*revenues):
    # newest-first, index 0 = current partial week
    return [{"revenue_cents": r, "orders": 10} for r in revenues]


class TestRevenueGauge:
    def test_flat_is_50(self):
        assert revenue_gauge(_weeks(500, 1000, 1000)) == 50

    def test_up_50pct_is_100(self):
        assert revenue_gauge(_weeks(500, 1500, 1000)) == 100

    def test_down_50pct_is_0(self):
        assert revenue_gauge(_weeks(500, 500, 1000)) == 0

    def test_skips_partial_current_week(self):
        # Current week (idx 0) tanking must not affect the score.
        assert revenue_gauge(_weeks(1, 1000, 1000)) == 50

    def test_none_without_enough_weeks(self):
        assert revenue_gauge(_weeks(500, 1000)) is None
        assert revenue_gauge(_weeks(500, 1000, 0)) is None


class TestProfitGauge:
    def test_clean_store_is_100(self):
        assert profit_gauge(100_000, 0, 0, 0) == 100

    def test_discount_leak_penalized(self):
        # 20k discounts on 80k net → leak 20% → −40 (capped)
        assert profit_gauge(80_000, 20_000, 0, 0) == 60

    def test_cod_losses_penalized(self):
        # 30% of COD value rejected → −30
        assert profit_gauge(100_000, 0, 30_000, 100_000) == 70

    def test_none_without_revenue(self):
        assert profit_gauge(0, 0, 0, 0) is None


class TestInventoryGauge:
    def test_all_in_stock(self):
        stock = [{"quantity": 50} for _ in range(10)]
        assert inventory_gauge(stock) == 100

    def test_out_and_low_penalized(self):
        stock = [{"quantity": 50}] * 6 + [{"quantity": 0}] * 2 + [{"quantity": 3}] * 2
        # (10 − 2 − 0.5·2)/10 = 70%
        assert inventory_gauge(stock) == 70

    def test_none_empty_catalog(self):
        assert inventory_gauge([]) is None


class TestMarketingGauge:
    def test_perfect(self):
        # Fully tagged + even 4-channel mix (HHI 0.25 ≤ 0.4)
        g = marketing_gauge(100.0, {"a": 25, "b": 25, "c": 25, "d": 25})
        assert g == 100

    def test_single_channel_halved(self):
        # HHI = 1 → diversity 0; hygiene 100 → 50
        assert marketing_gauge(100.0, {"direct": 100}) == 50

    def test_none_without_any_data(self):
        assert marketing_gauge(None, {}) is None


class TestCustomerGauge:
    def test_all_vip(self):
        assert customer_gauge({"vip": 10}) == 100

    def test_mixed(self):
        # (100·2 + 75·4 + 5·4)/10 = 52
        assert customer_gauge({"vip": 2, "active": 4, "churned": 4}) == 52

    def test_none_empty(self):
        assert customer_gauge({}) is None


class TestBriefing:
    def _fmt(self, cents):
        return f"{cents / 100:,.0f} EGP"

    def test_month_band_variant(self):
        b = briefing(
            datetime(2026, 7, 15, 9, 0, tzinfo=CAIRO),
            {"lower": 1, "upper": 3},
            {"lower_cents": 100_000, "upper_cents": 140_000},
            2,
            3,
            self._fmt,
        )
        assert b["en"].startswith("Good morning.")
        assert "1,000 EGP–1,400 EGP" in b["en"]
        assert "2 issues" in b["en"]
        assert "3 opportunities are waiting" in b["en"]
        assert "صباح الخير" in b["ar"]
        assert "3 فرص" in b["ar"]

    def test_orders_fallback_and_zero_counts(self):
        b = briefing(
            datetime(2026, 7, 15, 20, 0, tzinfo=CAIRO),
            {"lower": 5, "upper": 9},
            None,
            0,
            0,
            self._fmt,
        )
        assert b["en"].startswith("Good evening.")
        assert "5–9 orders today" in b["en"]
        assert "no issues need attention" in b["en"]

    def test_no_data_variant(self):
        b = briefing(
            datetime(2026, 7, 15, 14, 0, tzinfo=CAIRO), None, None, 1, 0, self._fmt
        )
        assert "Not enough history" in b["en"]
        assert "1 issue needs attention" in b["en"]
