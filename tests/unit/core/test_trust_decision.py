"""Unit tests for the canonical trust-decision FSM entity (Phase C)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.entities.trust_decision import (
    TRUST_DECISION_TERMINAL_STATES,
    InvalidTrustDecisionTransition,
    TrustDecision,
    TrustDecisionState,
    assert_can_transition,
    is_terminal,
)


class TestTransitionTable:
    def test_new_to_scored_is_legal(self):
        assert_can_transition(TrustDecisionState.NEW, TrustDecisionState.SCORED)

    def test_new_to_decision_is_illegal(self):
        with pytest.raises(InvalidTrustDecisionTransition):
            assert_can_transition(
                TrustDecisionState.NEW, TrustDecisionState.AUTO_APPROVED
            )

    @pytest.mark.parametrize(
        "target",
        [
            TrustDecisionState.AUTO_APPROVED,
            TrustDecisionState.CONFIRM_PENDING,
            TrustDecisionState.HELD,
            TrustDecisionState.BLOCKED,
            TrustDecisionState.CANCELLED,
        ],
    )
    def test_scored_to_each_decision_is_legal(self, target):
        assert_can_transition(TrustDecisionState.SCORED, target)

    def test_confirm_pending_resolves_to_confirmed_or_cancelled(self):
        assert_can_transition(
            TrustDecisionState.CONFIRM_PENDING, TrustDecisionState.CONFIRMED
        )
        assert_can_transition(
            TrustDecisionState.CONFIRM_PENDING, TrustDecisionState.CANCELLED
        )

    def test_confirm_pending_to_held_is_illegal(self):
        with pytest.raises(InvalidTrustDecisionTransition):
            assert_can_transition(
                TrustDecisionState.CONFIRM_PENDING, TrustDecisionState.HELD
            )

    def test_held_resolves_to_confirmed_or_cancelled(self):
        assert_can_transition(TrustDecisionState.HELD, TrustDecisionState.CONFIRMED)
        assert_can_transition(TrustDecisionState.HELD, TrustDecisionState.CANCELLED)

    @pytest.mark.parametrize("state", sorted(TRUST_DECISION_TERMINAL_STATES))
    def test_terminal_states_have_no_outgoing_transitions(self, state):
        assert is_terminal(state) is True
        with pytest.raises(InvalidTrustDecisionTransition):
            assert_can_transition(state, TrustDecisionState.SCORED)


class TestTrustDecisionAggregate:
    def test_starts_new_with_empty_history(self):
        d = TrustDecision()
        assert d.state == TrustDecisionState.NEW
        assert d.history == []
        assert d.is_terminal is False

    def test_transition_records_history(self):
        d = TrustDecision()
        d.transition_to(TrustDecisionState.SCORED, reason="scored")
        d.transition_to(TrustDecisionState.CONFIRM_PENDING, reason="medium risk")
        assert d.state == TrustDecisionState.CONFIRM_PENDING
        assert [(h.from_state, h.to_state) for h in d.history] == [
            (TrustDecisionState.NEW, TrustDecisionState.SCORED),
            (TrustDecisionState.SCORED, TrustDecisionState.CONFIRM_PENDING),
        ]
        assert d.history[0].reason == "scored"
        assert all(h.at is not None for h in d.history)

    def test_illegal_transition_raises_and_does_not_mutate(self):
        d = TrustDecision()
        with pytest.raises(InvalidTrustDecisionTransition):
            d.transition_to(TrustDecisionState.CONFIRMED)
        assert d.state == TrustDecisionState.NEW
        assert d.history == []

    def test_full_confirm_path_to_terminal_then_frozen(self):
        d = TrustDecision()
        d.transition_to(TrustDecisionState.SCORED)
        d.transition_to(TrustDecisionState.CONFIRM_PENDING)
        d.transition_to(TrustDecisionState.CONFIRMED)
        assert d.is_terminal is True
        with pytest.raises(InvalidTrustDecisionTransition):
            d.transition_to(TrustDecisionState.CANCELLED)

    def test_injected_timestamp_is_used(self):
        d = TrustDecision()
        ts = datetime(2026, 6, 3, tzinfo=UTC)
        d.transition_to(TrustDecisionState.SCORED, at=ts)
        assert d.history[0].at == ts
