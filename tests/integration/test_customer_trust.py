"""Tests for the deterministic customer-trust formula (backend-022).

The formula is the Shopify-app-visible side of spec 010 — the same
decision the badge UI consumes. Snapshot-style coverage: pin the
output for a representative set of trust profiles so the formula
doesn't silently drift between releases (constitution Principle IV).
"""

from __future__ import annotations

import pytest

from src.application.services.customer_trust_formula import (
    AUTO_APPROVE_KILL_SWITCH_MAX_RTO_RATE,
    AUTO_APPROVE_KILL_SWITCH_MIN_SAMPLE,
    AUTO_APPROVE_RISK_CAP,
    TrustInputs,
    compute_customer_trust,
    kill_switch_should_disable,
    should_auto_approve_trusted,
)

# ---------------------------------------------------------------------------
# Formula snapshot tests — pin the output for canonical profiles
# ---------------------------------------------------------------------------


class TestTrustFormulaSnapshots:
    """Spec 010 SC-001 — formula reproducibility for canonical profiles."""

    def test_no_history_yields_zero_no_badge(self):
        """Brand-new customer → score 0, tier 'none', no badge rendered."""
        result = compute_customer_trust(TrustInputs())
        assert result.customer_trust == 0
        assert result.trust_tier == "none"
        assert result.negative_adjustment_count == 0
        assert result.trust_lookup_degraded is False

    def test_gold_tier_high_volume_clean(self):
        """10 deliveries + 5 prepaid + 80% WA + 8 net pos → Gold (clamped 100)."""
        result = compute_customer_trust(
            TrustInputs(
                successful_deliveries=10,
                prepaid_orders=5,
                whatsapp_response_rate_pct=80,
                network_positive_events=8,
            )
        )
        # Raw = 40 + 30 + 8 + 24 = 102 → clamped 100, Gold.
        assert result.customer_trust == 100
        assert result.trust_tier == "gold"
        assert result.negative_adjustment_count == 0

    def test_silver_with_negative_adjustment(self):
        """Same Gold profile + 3 network RTOs → Silver (78)."""
        result = compute_customer_trust(
            TrustInputs(
                successful_deliveries=10,
                prepaid_orders=5,
                whatsapp_response_rate_pct=80,
                network_positive_events=8,
                network_negative_events=3,
            )
        )
        # Raw = 102 - 24 = 78 → Silver.
        assert result.customer_trust == 78
        assert result.trust_tier == "silver"
        assert result.negative_adjustment_count == 3

    def test_bronze_modest_local_only(self):
        """3 deliveries + 1 prepaid → 18 raw → 'new' tier."""
        result = compute_customer_trust(
            TrustInputs(
                successful_deliveries=3,
                prepaid_orders=1,
            )
        )
        # Raw = 12 + 6 = 18 → 'new' (1 ≤ score < 30).
        assert result.customer_trust == 18
        assert result.trust_tier == "new"

    def test_bronze_clean_history(self):
        """5 deliveries + 2 prepaid → 32 raw → Bronze."""
        result = compute_customer_trust(
            TrustInputs(
                successful_deliveries=5,
                prepaid_orders=2,
            )
        )
        # Raw = 20 + 12 = 32 → Bronze.
        assert result.customer_trust == 32
        assert result.trust_tier == "bronze"

    def test_clamps_negative_to_zero(self):
        """Heavy negative adjustment never produces a negative score."""
        result = compute_customer_trust(
            TrustInputs(
                successful_deliveries=1,
                network_negative_events=20,  # -160 penalty
            )
        )
        # Raw = 4 - 160 = -156 → clamped 0.
        # Has history, so tier is 'new' not 'none'.
        assert result.customer_trust == 0
        assert result.trust_tier == "new"
        assert result.negative_adjustment_count == 20

    def test_lookup_degraded_flag_propagates(self):
        """When network signal lookup fell back to local-only, expose the flag."""
        result = compute_customer_trust(
            TrustInputs(successful_deliveries=2),
            trust_lookup_degraded=True,
        )
        assert result.trust_lookup_degraded is True


# ---------------------------------------------------------------------------
# Auto-approve gating logic — spec 010 FR-002 + CL-001 + CL-004
# ---------------------------------------------------------------------------


class TestAutoApproveGating:
    """Spec 010 FR-002 + CL-001 — every precondition must pass."""

    @pytest.fixture
    def baseline_inputs(self):
        return {
            "customer_trust": 85,
            "risk_score": 55,
            "auto_approve_on_trust_enabled": True,
            "auto_approve_trust_threshold": 80,
            "install_grace_active": False,
            "manual_approve_count": 10,
        }

    def test_baseline_passes_all_gates(self, baseline_inputs):
        assert should_auto_approve_trusted(**baseline_inputs) is True

    def test_disabled_toggle_blocks(self, baseline_inputs):
        baseline_inputs["auto_approve_on_trust_enabled"] = False
        assert should_auto_approve_trusted(**baseline_inputs) is False

    def test_below_threshold_blocks(self, baseline_inputs):
        baseline_inputs["customer_trust"] = 79
        assert should_auto_approve_trusted(**baseline_inputs) is False

    def test_above_risk_cap_blocks(self, baseline_inputs):
        """High trust + very high risk → still goes to manual review."""
        baseline_inputs["risk_score"] = AUTO_APPROVE_RISK_CAP + 1
        assert should_auto_approve_trusted(**baseline_inputs) is False

    def test_install_grace_blocks(self, baseline_inputs):
        """Constitution v1.1.0 — first 30d post-install gate applies here too."""
        baseline_inputs["install_grace_active"] = True
        assert should_auto_approve_trusted(**baseline_inputs) is False

    def test_under_5_manual_approves_blocks(self, baseline_inputs):
        """Spec 010 CL-001 — counting approves, not cancels."""
        baseline_inputs["manual_approve_count"] = 4
        assert should_auto_approve_trusted(**baseline_inputs) is False

    def test_exactly_5_manual_approves_passes(self, baseline_inputs):
        """The threshold is ≥ 5, not > 5."""
        baseline_inputs["manual_approve_count"] = 5
        assert should_auto_approve_trusted(**baseline_inputs) is True

    def test_threshold_at_risk_cap_boundary_passes(self, baseline_inputs):
        """risk_score == cap is allowed; only > cap blocks."""
        baseline_inputs["risk_score"] = AUTO_APPROVE_RISK_CAP
        assert should_auto_approve_trusted(**baseline_inputs) is True


# ---------------------------------------------------------------------------
# Kill-switch logic — spec 010 CL-002 maintainer-confirmed thresholds
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_below_minimum_sample_dormant(self):
        """≥ 20 sample required before evaluation per CL-002."""
        # 4 RTOs out of 19 = 21% but we don't even evaluate.
        assert kill_switch_should_disable(auto_approve_count=19, rto_count=4) is False

    def test_at_minimum_sample_evaluates(self):
        """20 samples enough to evaluate."""
        # 2 RTOs out of 20 = 10% → exceeds 5% → fires.
        assert kill_switch_should_disable(auto_approve_count=20, rto_count=2) is True

    def test_at_threshold_does_not_fire(self):
        """5% rate is the threshold; only > 5% trips."""
        # 1 RTO out of 20 = 5% → does NOT trip (per > comparison).
        assert kill_switch_should_disable(auto_approve_count=20, rto_count=1) is False

    def test_zero_rto_never_fires(self):
        assert kill_switch_should_disable(auto_approve_count=100, rto_count=0) is False

    def test_zero_sample_never_fires(self):
        assert kill_switch_should_disable(auto_approve_count=0, rto_count=0) is False


# ---------------------------------------------------------------------------
# Constants are stable (snapshot the public API)
# ---------------------------------------------------------------------------


class TestConstantsStable:
    """Spec 010 SC-002 — the kill-switch params are maintainer-confirmed."""

    def test_min_sample_is_20(self):
        assert AUTO_APPROVE_KILL_SWITCH_MIN_SAMPLE == 20

    def test_max_rto_rate_is_5pct(self):
        assert AUTO_APPROVE_KILL_SWITCH_MAX_RTO_RATE == 0.05

    def test_risk_cap_is_90(self):
        assert AUTO_APPROVE_RISK_CAP == 90
