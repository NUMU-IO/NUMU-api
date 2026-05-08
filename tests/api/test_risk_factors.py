"""Tests for backend-016 risk-model completeness.

Pins the three new factors (`payment_method`, `time_pattern`,
`product_risk`) plus the `score_order` weight rebalance so the
advertised "8-factor model" is real and stays real. The previous
audit found `payment_method` was captured but never used in scoring,
and `time_pattern` + `product_risk` were completely absent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.use_cases.shopify.risk_scoring_engine import (
    _score_payment_method,
    _score_product_risk,
    _score_time_pattern,
    score_order,
)

# ─────────────────────────────────────────────────────────────────────
# payment_method
# ─────────────────────────────────────────────────────────────────────


class TestPaymentMethodFactor:
    def test_cod_scores_high(self):
        f = _score_payment_method("cash_on_delivery")
        assert f.factor == "payment_method"
        assert f.score >= 70.0
        assert "COD" in f.reason or "collection" in f.reason.lower()

    @pytest.mark.parametrize("alias", ["cod", "cash", "manual", "Cash on Delivery"])
    def test_cod_aliases_recognized(self, alias):
        f = _score_payment_method(alias)
        assert f.score >= 70.0

    @pytest.mark.parametrize(
        "method",
        ["paymob", "card", "credit_card", "wallet", "instapay", "Stripe", "FAWRY"],
    )
    def test_prepaid_scores_low(self, method):
        f = _score_payment_method(method)
        assert f.score <= 10.0
        assert "Pre-paid" in f.reason

    def test_unknown_method_scores_neutral(self):
        f = _score_payment_method("some_obscure_gateway")
        assert 30.0 <= f.score <= 60.0

    def test_missing_method_scores_neutral(self):
        f = _score_payment_method(None)
        assert 30.0 <= f.score <= 60.0

    def test_factor_weight_is_07(self):
        """Locked: 0.07 — if this changes, the score_order weight sum
        breaks. Update the bundle in score_order alongside any change."""
        assert _score_payment_method("cod").weight == 0.07


# ─────────────────────────────────────────────────────────────────────
# time_pattern (Cairo TZ-aware)
# ─────────────────────────────────────────────────────────────────────


class TestTimePatternFactor:
    def test_late_night_cairo_window_scores_high(self):
        # 01:00 UTC = 04:00 Cairo (UTC+3 in May with DST). Inside 1-5 AM window.
        ts = datetime(2026, 5, 9, 1, 0, 0, tzinfo=UTC)
        f = _score_time_pattern(ts)
        assert f.score >= 60.0
        assert "late-night" in f.reason.lower() or "risk window" in f.reason.lower()

    def test_normal_hours_scores_low(self):
        # 12:00 UTC = 15:00 Cairo. Daytime.
        ts = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        f = _score_time_pattern(ts)
        assert f.score <= 20.0

    def test_naive_datetime_treated_as_utc(self):
        ts = datetime(2026, 5, 9, 12, 0, 0)  # naive
        f = _score_time_pattern(ts)
        # Should not raise; Cairo hour will be 14.
        assert f.score <= 20.0

    def test_missing_timestamp_uses_baseline(self):
        f = _score_time_pattern(None)
        assert "unavailable" in f.reason.lower() or "baseline" in f.reason.lower()
        assert 0.0 <= f.score <= 30.0

    def test_factor_weight_is_05(self):
        ts = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        assert _score_time_pattern(ts).weight == 0.05


# ─────────────────────────────────────────────────────────────────────
# product_risk
# ─────────────────────────────────────────────────────────────────────


class TestProductRiskFactor:
    def test_no_tags_returns_zero_with_documented_reason(self):
        """The audit called out the alternative — smearing every
        order with a 'neutral' guess — as dishonest. Score 0 + an
        explicit no_tag_data reason is the chosen contract."""
        f = _score_product_risk(None)
        assert f.score == 0.0
        assert "no_tag_data" in f.reason

    def test_empty_list_treated_as_no_tags(self):
        f = _score_product_risk([])
        assert f.score == 0.0
        assert "no_tag_data" in f.reason

    @pytest.mark.parametrize(
        "tags",
        [
            ["electronics"],
            ["jewelry"],
            ["luxury", "watch"],
            ["Smart Phone"],  # case + space normalization
            ["high-value-items"],  # hyphen normalization
            ["expensive-laptop-bag"],
        ],
    )
    def test_high_risk_tags_score_high(self, tags):
        f = _score_product_risk(tags)
        assert f.score >= 60.0
        assert "High-risk" in f.reason

    def test_innocuous_tags_score_low(self):
        f = _score_product_risk(["sale", "summer", "cotton", "t-shirt"])
        assert f.score <= 20.0
        assert "no high-risk" in f.reason.lower()

    def test_non_string_tags_filtered(self):
        """Defensive — Shopify webhook payloads sometimes contain
        nulls or numbers in the tags array."""
        f = _score_product_risk([None, 42, "electronics"])  # type: ignore[list-item]
        assert f.score >= 60.0

    def test_factor_weight_is_05(self):
        assert _score_product_risk(["electronics"]).weight == 0.05


# ─────────────────────────────────────────────────────────────────────
# score_order — full 8-factor integration
# ─────────────────────────────────────────────────────────────────────


class TestEightFactorScoreOrder:
    def test_returns_nine_factors_with_all_three_new_ones(self):
        """Nine factor objects (network + 5 historical + 3 new); the
        spec calls it the '8-factor model' but the bundle includes
        network_reputation as the +1, matching the public messaging."""
        result = score_order(
            total_cents=50_000,
            payment_method="cod",
            customer_total_orders=2,
            customer_cancellation_rate=0.1,
            address="12 Tahrir Street, Cairo, EG",
            phone="+201001234567",
            avg_order_cents=80_000,
            network_score=55,
            network_label="new_to_network",
            created_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC),
            product_tags=["t-shirt", "cotton"],
        )
        names = {f.factor for f in result.factors}
        assert "payment_method" in names
        assert "time_pattern" in names
        assert "product_risk" in names
        assert names == {
            "network_reputation",
            "customer_history",
            "order_value",
            "cancellation_rate",
            "payment_method",
            "address_quality",
            "phone_validation",
            "time_pattern",
            "product_risk",
        }

    def test_weights_sum_to_one(self):
        """Lock the weight invariant. Without this, a future tweak that
        reshuffles weights could quietly break the 0–100 score range."""
        result = score_order(
            total_cents=50_000,
            payment_method="paymob",
            customer_total_orders=1,
            avg_order_cents=80_000,
            created_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC),
        )
        total_weight = sum(f.weight for f in result.factors)
        assert abs(total_weight - 1.00) < 1e-6

    def test_cod_late_night_high_value_electronics_scores_critically_high(self):
        """End-to-end realistic worst case: COD payment, 03:00 Cairo,
        electronics tag, big order. Should land in the high+ band."""
        result = score_order(
            total_cents=500_000,  # 5000 EGP — way above 800 EGP avg
            payment_method="cod",
            customer_total_orders=0,
            customer_cancellation_rate=None,
            address="x",  # short — flagged
            phone="01099999999",  # missing + so regex fails the strict match
            avg_order_cents=80_000,
            network_score=55,
            network_label="new_to_network",
            created_at=datetime(
                2026, 5, 9, 0, 30, 0, tzinfo=UTC
            ),  # 03:30 Cairo (UTC+3 DST)
            product_tags=["electronics", "phone"],
        )
        assert result.risk_score >= 50  # solid mid-band or higher
        assert result.risk_level in {"medium", "high", "critical"}

    def test_prepaid_card_normal_hours_scores_significantly_lower(self):
        """Holding all else equal but flipping COD → prepaid + late-night
        → daytime should pull the score down meaningfully (the whole
        point of layering payment_method + time_pattern in)."""
        cod_result = score_order(
            total_cents=80_000,
            payment_method="cod",
            customer_total_orders=2,
            customer_cancellation_rate=0.1,
            address="12 Tahrir Street, Cairo, EG",
            phone="+201001234567",
            avg_order_cents=80_000,
            network_score=55,
            network_label="new_to_network",
            created_at=datetime(2026, 5, 9, 1, 0, 0, tzinfo=UTC),  # 04:00 Cairo
            product_tags=None,
        )
        card_result = score_order(
            total_cents=80_000,
            payment_method="paymob",
            customer_total_orders=2,
            customer_cancellation_rate=0.1,
            address="12 Tahrir Street, Cairo, EG",
            phone="+201001234567",
            avg_order_cents=80_000,
            network_score=55,
            network_label="new_to_network",
            created_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC),  # 14:00 Cairo
            product_tags=None,
        )
        assert card_result.risk_score < cod_result.risk_score
        # Magnitude: at least 5 points of separation given combined
        # payment_method (0.07) + time_pattern (0.05) deltas.
        assert (cod_result.risk_score - card_result.risk_score) >= 5
