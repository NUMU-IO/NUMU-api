"""Unit tests for the customer health scorer + state machine (AI-3)."""

from datetime import UTC, datetime, timedelta

from src.application.services.customer_health_service import (
    median_interpurchase_gap,
    score_store_customers,
)

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def _c(cid, orders, spent, last_days_ago, first_days_ago=None, **over):
    row = {
        "customer_id": cid,
        "orders": orders,
        "total_spent_cents": spent,
        "first_at": NOW - timedelta(days=first_days_ago or last_days_ago + 30 * orders),
        "last_at": NOW - timedelta(days=last_days_ago),
        "coupon_orders": 0,
        "returned_orders": 0,
        "spend_last_90": spent,
        "spend_prior_90": 0,
        "events_30d": 0,
    }
    row.update(over)
    return row


class TestMedianGap:
    def test_fallback_below_three_samples(self):
        assert median_interpurchase_gap([_c("a", 1, 100, 5)]) == 30.0

    def test_measures_repeat_rhythm(self):
        rows = [
            _c("a", 3, 300, 5, first_days_ago=45),  # gap 20
            _c("b", 2, 200, 10, first_days_ago=40),  # gap 30
            _c("c", 5, 500, 2, first_days_ago=42),  # gap 10
        ]
        assert median_interpurchase_gap(rows) == 20.0


class TestStates:
    def test_vip_top_spender_recent(self):
        rows = [_c("vip", 8, 50_000_00, 2)] + [
            _c(f"x{i}", 1, 100_00, 20) for i in range(9)
        ]
        scored = {s["customer_id"]: s for s in score_store_customers(rows, NOW, 500_00)}
        assert scored["vip"]["state"] == "vip"
        assert scored["vip"]["score"] >= 80

    def test_at_risk_when_stale_beyond_gap(self):
        rows = [
            _c("stale", 4, 4_000_00, 120, first_days_ago=200),
            _c("a", 3, 300, 5, first_days_ago=45),
            _c("b", 2, 200, 10, first_days_ago=40),
            _c("c", 5, 500, 2, first_days_ago=42),
        ]
        scored = {s["customer_id"]: s for s in score_store_customers(rows, NOW, 100_00)}
        # median gap 20d → 120d since last is way past 1.5×
        assert scored["stale"]["state"] in ("at_risk", "churned")

    def test_coupon_hunter(self):
        rows = [_c("ch", 5, 500_00, 5, coupon_orders=5)] + [
            _c(f"x{i}", 1, 1_000_00, 10) for i in range(4)
        ]
        scored = {s["customer_id"]: s for s in score_store_customers(rows, NOW, 200_00)}
        assert scored["ch"]["state"] == "coupon_hunter"

    def test_high_value_prospect_one_big_recent_order(self):
        rows = [_c("hvp", 1, 1_000_00, 10)] + [
            _c(f"x{i}", 1, 100_00, 10) for i in range(4)
        ]
        scored = {s["customer_id"]: s for s in score_store_customers(rows, NOW, 200_00)}
        assert scored["hvp"]["state"] == "high_value_prospect"

    def test_churned_one_timer_long_gone(self):
        rows = [_c("gone", 1, 100_00, 200)] + [
            _c(f"x{i}", 1, 100_00, 5) for i in range(4)
        ]
        scored = {s["customer_id"]: s for s in score_store_customers(rows, NOW, 100_00)}
        assert scored["gone"]["state"] == "churned"

    def test_reliability_penalty_applies(self):
        rows = [
            _c("ret", 3, 300_00, 5, returned_orders=2),
            _c("ok", 3, 300_00, 5),
        ]
        scored = {s["customer_id"]: s for s in score_store_customers(rows, NOW, 100_00)}
        assert scored["ok"]["score"] > scored["ret"]["score"]

    def test_sorted_by_score_desc(self):
        rows = [_c("low", 1, 50_00, 90), _c("high", 6, 5_000_00, 1)]
        out = score_store_customers(rows, NOW, 100_00)
        assert out[0]["customer_id"] == "high"
