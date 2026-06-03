"""Unit tests for the unified trust-decision computation (Phase C).

These pin the decision boundaries that both surfaces will share once the FSM
is cut over — the spec a due-diligence reviewer reads to answer "what does the
system do at score X?".
"""

from __future__ import annotations

from src.application.services.trust_decision_service import DecisionInputs, decide
from src.core.entities.trust_decision import TrustDecisionState


def _trusted(**over) -> DecisionInputs:
    base = {
        "risk_score": 40,
        "customer_trust": 85,
        "auto_approve_on_trust_enabled": True,
        "auto_approve_trust_threshold": 80,
        "install_grace_active": False,
        "manual_approve_count": 10,
    }
    base.update(over)
    return DecisionInputs(**base)


class TestAutoApprove:
    def test_trusted_buyer_auto_approves(self):
        assert decide(_trusted()) == TrustDecisionState.AUTO_APPROVED

    def test_trusted_but_risk_over_cap_falls_through_to_ladder(self):
        # risk 95 > 90 cap → not trusted-auto-approved; final + gated → CANCELLED.
        d = _trusted(risk_score=95, score_type="final", manual_cancel_count=5)
        assert decide(d) == TrustDecisionState.CANCELLED

    def test_trust_below_threshold_does_not_auto_approve(self):
        d = _trusted(customer_trust=70)  # threshold is 80
        assert decide(d) == TrustDecisionState.CONFIRM_PENDING  # risk 40

    def test_low_risk_auto_approves_without_trust(self):
        assert decide(DecisionInputs(risk_score=10)) == TrustDecisionState.AUTO_APPROVED


class TestCancelSafetyGates:
    def test_high_risk_final_past_grace_with_cancels_cancels(self):
        d = DecisionInputs(
            risk_score=95,
            score_type="final",
            install_grace_active=False,
            manual_cancel_count=5,
        )
        assert decide(d) == TrustDecisionState.CANCELLED

    def test_high_risk_preliminary_holds_not_cancels(self):
        d = DecisionInputs(
            risk_score=95,
            score_type="preliminary",
            install_grace_active=False,
            manual_cancel_count=5,
        )
        assert decide(d) == TrustDecisionState.HELD

    def test_high_risk_in_install_grace_holds_not_cancels(self):
        d = DecisionInputs(
            risk_score=95,
            score_type="final",
            install_grace_active=True,
            manual_cancel_count=5,
        )
        assert decide(d) == TrustDecisionState.HELD

    def test_high_risk_without_five_manual_cancels_holds(self):
        d = DecisionInputs(
            risk_score=95,
            score_type="final",
            install_grace_active=False,
            manual_cancel_count=4,
        )
        assert decide(d) == TrustDecisionState.HELD


class TestNativeBlockMode:
    def test_block_high_confidence_blocks(self):
        d = DecisionInputs(
            risk_score=75,
            block_enabled=True,
            block_threshold=70,
            confidence="high",
            min_confidence_to_act="medium",
        )
        assert decide(d) == TrustDecisionState.BLOCKED

    def test_block_low_confidence_does_not_block(self):
        # Never block on thin data — falls through to HELD (75 >= 70).
        d = DecisionInputs(
            risk_score=75,
            block_enabled=True,
            block_threshold=70,
            confidence="low",
            min_confidence_to_act="medium",
        )
        assert decide(d) == TrustDecisionState.HELD

    def test_block_disabled_uses_ladder(self):
        d = DecisionInputs(risk_score=75, block_enabled=False, confidence="high")
        assert decide(d) == TrustDecisionState.HELD


class TestRiskLadder:
    def test_medium_risk_confirm_pending(self):
        assert (
            decide(DecisionInputs(risk_score=50)) == TrustDecisionState.CONFIRM_PENDING
        )

    def test_hold_band_lower_boundary(self):
        assert decide(DecisionInputs(risk_score=70)) == TrustDecisionState.HELD

    def test_confirm_lower_boundary(self):
        assert (
            decide(DecisionInputs(risk_score=30)) == TrustDecisionState.CONFIRM_PENDING
        )

    def test_just_below_confirm_auto_approves(self):
        assert decide(DecisionInputs(risk_score=29)) == TrustDecisionState.AUTO_APPROVED
