"""Unit tests for the moat-metrics rate math (Phase F — proof the moat works)."""

from __future__ import annotations

from src.application.services.moat_metrics_service import (
    compute_auto_approve_quality,
    compute_coverage,
    compute_cross_store_catch,
)


class TestCoverage:
    def test_reach_pct(self):
        m = compute_coverage(
            phones_tracked=1000, multi_store_phones=250, total_network_orders=5000
        )
        assert m["cross_store_reach_pct"] == 25.0
        assert m["phones_tracked"] == 1000
        assert m["total_network_orders"] == 5000

    def test_zero_phones_is_zero_not_error(self):
        m = compute_coverage(
            phones_tracked=0, multi_store_phones=0, total_network_orders=0
        )
        assert m["cross_store_reach_pct"] == 0.0


class TestCrossStoreCatch:
    def test_catch_pct(self):
        m = compute_cross_store_catch(
            phones_with_rtos=80, cross_store_phones_with_rtos=60
        )
        assert m["cross_store_catch_pct"] == 75.0
        assert m["cross_store_risk_signals"] == 60

    def test_no_rtos_is_zero(self):
        m = compute_cross_store_catch(
            phones_with_rtos=0, cross_store_phones_with_rtos=0
        )
        assert m["cross_store_catch_pct"] == 0.0


class TestAutoApproveQuality:
    def test_auto_approve_beats_baseline(self):
        # Auto-approved cohort: 2/100 = 2% RTO. Baseline COD: 12/100 = 12%.
        m = compute_auto_approve_quality(
            auto_approved=100,
            auto_approved_rtos=2,
            baseline_cod=100,
            baseline_cod_rtos=12,
        )
        assert m["auto_approved_rto_rate_pct"] == 2.0
        assert m["baseline_cod_rto_rate_pct"] == 12.0
        # Negative delta = the auto-approved cohort is BETTER than baseline.
        assert m["rto_rate_delta_pct"] == -10.0

    def test_zero_cohorts_are_zero(self):
        m = compute_auto_approve_quality(
            auto_approved=0,
            auto_approved_rtos=0,
            baseline_cod=0,
            baseline_cod_rtos=0,
        )
        assert m["auto_approved_rto_rate_pct"] == 0.0
        assert m["rto_rate_delta_pct"] == 0.0
