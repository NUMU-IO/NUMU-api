"""Predictions v1 (AI-5) — pure-math unit tests."""

from datetime import UTC, date, datetime, timedelta

from src.application.services.prediction_service import (
    cod_rejection_profile,
    ewma_velocity,
    month_revenue_band,
    predict_stockouts,
    repeat_probability,
    stockout_prediction,
    today_orders_band,
    wilson_interval,
)

TODAY = date(2026, 7, 15)
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


class TestStockouts:
    def test_velocity_blend(self):
        # 14/7d = 2/day recent, 28/28d = 1/day base → 0.6*2 + 0.4*1
        assert ewma_velocity(14, 28) == 1.6

    def test_basic_depletion(self):
        p = {
            "product_id": "a",
            "name": "Tee",
            "quantity": 16,
            "units_7d": 14,
            "units_28d": 28,
        }
        pred = stockout_prediction(p, TODAY)
        assert pred is not None
        assert pred["days_left"] == 10.0  # 16 / 1.6
        assert pred["run_out_date"] == "2026-07-25"
        assert pred["urgent"] is False
        assert pred["confidence"] == "high"
        # Poisson band: early < point < late
        assert pred["early_date"] < pred["run_out_date"]
        assert pred["late_date"] is None or pred["late_date"] > pred["run_out_date"]

    def test_urgent_inside_7_days(self):
        p = {
            "product_id": "a",
            "name": "Tee",
            "quantity": 5,
            "units_7d": 14,
            "units_28d": 28,
        }
        assert stockout_prediction(p, TODAY)["urgent"] is True

    def test_no_velocity_no_prediction(self):
        p = {
            "product_id": "a",
            "name": "Tee",
            "quantity": 10,
            "units_7d": 0,
            "units_28d": 0,
        }
        assert stockout_prediction(p, TODAY) is None

    def test_beyond_horizon_skipped(self):
        p = {
            "product_id": "a",
            "name": "Tee",
            "quantity": 1000,
            "units_7d": 7,
            "units_28d": 28,
        }
        assert stockout_prediction(p, TODAY) is None

    def test_low_confidence_thin_history(self):
        p = {
            "product_id": "a",
            "name": "Tee",
            "quantity": 5,
            "units_7d": 5,
            "units_28d": 6,
        }
        assert stockout_prediction(p, TODAY)["confidence"] == "low"

    def test_sorted_soonest_first_and_capped(self):
        products = [
            {
                "product_id": str(i),
                "name": f"P{i}",
                "quantity": 10 + i * 5,
                "units_7d": 14,
                "units_28d": 28,
            }
            for i in range(15)
        ]
        preds = predict_stockouts(products, TODAY, limit=10)
        assert len(preds) == 10
        assert preds == sorted(preds, key=lambda x: x["days_left"])


class TestBands:
    def test_month_band_needs_14_days(self):
        assert month_revenue_band([100.0] * 10, 500, 10) is None

    def test_month_band_flat_series(self):
        band = month_revenue_band([10_000.0] * 30, 50_000, 5)
        assert band is not None
        # 5 remaining flat days ≈ 50k more; expected ≈ 100k
        assert 90_000 <= band["expected_cents"] <= 110_000
        assert band["lower_cents"] <= band["expected_cents"] <= band["upper_cents"]
        assert band["mtd_cents"] == 50_000

    def test_month_closed_returns_mtd(self):
        band = month_revenue_band([10_000.0] * 30, 123, 0)
        assert band["expected_cents"] == 123
        assert band["remaining_days"] == 0

    def test_today_orders_band(self):
        band = today_orders_band([10.0] * 30)
        assert band is not None
        assert band["lower"] <= band["predicted"] <= band["upper"]
        assert 8 <= band["predicted"] <= 12


class TestRepeat:
    def _rows(self, n_repeat, n_single, gap_days=20.0):
        rows = []
        for i in range(n_repeat):
            first = NOW - timedelta(days=gap_days * 2)
            rows.append({
                "customer_id": f"r{i}",
                "orders": 3,
                "total_spent_cents": 300,
                "first_at": first,
                "last_at": first + timedelta(days=gap_days * 2),
            })
        for i in range(n_single):
            rows.append({
                "customer_id": f"s{i}",
                "orders": 1,
                "total_spent_cents": 100,
                "first_at": NOW,
                "last_at": NOW,
            })
        return rows

    def test_empirical_profile(self):
        prof = repeat_probability(self._rows(40, 60), NOW)
        assert prof["repeat_rate_pct"] == 40.0
        assert prof["median_gap_days"] == 20.0
        # all gaps ≤30 → P(next 30d) = repeat_rate × 1.0
        assert prof["p_next_30d_pct"] == 40.0
        assert prof["confidence"] == "medium"

    def test_long_gaps_lower_p30(self):
        prof = repeat_probability(self._rows(40, 60, gap_days=45.0), NOW)
        assert prof["p_next_30d_pct"] == 0.0

    def test_empty(self):
        prof = repeat_probability([], NOW)
        assert prof["repeat_rate_pct"] == 0.0
        assert prof["confidence"] == "low"


class TestCod:
    def test_wilson_small_sample_is_wide(self):
        lo, hi = wilson_interval(1, 4)
        assert lo < 0.10
        assert hi > 0.60

    def test_profile_shrinks_small_governorates(self):
        rows = [
            {"governorate": "cairo", "resolved": 90, "returned": 9},
            {"governorate": "aswan", "resolved": 2, "returned": 1},
        ]
        prof = cod_rejection_profile(rows, {"orders": 10, "value_cents": 100_000})
        assert prof is not None
        store_rate = prof["store_rate_pct"]  # 10/92 ≈ 10.9%
        aswan = next(g for g in prof["by_governorate"] if g["governorate"] == "aswan")
        assert aswan["rate_pct"] == 50.0
        # Shrunk toward the store rate — far below the raw 50%
        assert aswan["shrunk_rate_pct"] < 25.0
        assert aswan["shrunk_rate_pct"] > store_rate / 2
        assert prof["expected_loss_cents"] == round(100_000 * 10 / 92)

    def test_too_few_resolved_returns_none(self):
        rows = [{"governorate": "cairo", "resolved": 3, "returned": 1}]
        assert cod_rejection_profile(rows, {"orders": 0, "value_cents": 0}) is None
