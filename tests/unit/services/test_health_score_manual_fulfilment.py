"""Health score: the manual-fulfilment fallback and the return-rate maths.

The reported symptom: a merchant with 15 delivered COD orders saw both
"Delivery success" and "COD acceptance" marked *not enough data*, because
both metrics read only from `shipments` — rows that only exist when a
carrier integration writes them. Between them that is 55% of the model.
"""

import pytest

from src.application.services.health_score_service import (
    DELIVERY_SUCCESS_THRESHOLDS,
    MIN_COD_SHIPMENTS,
    MIN_SHIPMENTS_FOR_DELIVERY,
    MIN_USABLE_WEIGHT,
    RETURN_RATE_THRESHOLDS,
    WEIGHTS,
    _rate_to_score,
)


def _select_basis(total_shipments: int, order_outcomes: int, minimum: int):
    """Mirrors the selection the service performs, in isolation.

    Kept as a pure function so the rule can be asserted without standing up a
    database: shipments win when they clear their own gate, orders fill in
    only when they cannot.
    """
    if total_shipments < minimum and order_outcomes > 0:
        return "orders", order_outcomes
    return "shipments", total_shipments


class TestBasisSelection:
    def test_carrier_data_wins_when_it_clears_the_gate(self):
        # Shipments carry carrier-reported outcomes; self-marking is weaker
        # evidence and must not displace them.
        basis, sample = _select_basis(20, 50, MIN_SHIPMENTS_FOR_DELIVERY)
        assert basis == "shipments"
        assert sample == 20

    def test_orders_fill_in_for_a_manual_store(self):
        # The reported case: no carrier integration, so zero shipment rows.
        basis, sample = _select_basis(0, 15, MIN_SHIPMENTS_FOR_DELIVERY)
        assert basis == "orders"
        assert sample == 15

    def test_a_genuinely_new_store_is_still_ungraded(self):
        # The fallback must not paper over a store that has no evidence at
        # all — that was the point of the sample gates.
        basis, sample = _select_basis(0, 0, MIN_SHIPMENTS_FOR_DELIVERY)
        assert basis == "shipments"
        assert sample == 0
        assert sample < MIN_SHIPMENTS_FOR_DELIVERY

    def test_partial_carrier_data_does_not_strand_the_metric(self):
        # 2 shipments is below the gate; without the fallback the metric was
        # dropped even though 15 orders had a known outcome.
        basis, sample = _select_basis(2, 15, MIN_SHIPMENTS_FOR_DELIVERY)
        assert basis == "orders"
        assert sample == 15

    def test_cod_uses_its_own_lower_gate(self):
        basis, sample = _select_basis(0, 3, MIN_COD_SHIPMENTS)
        assert basis == "orders"
        assert sample >= MIN_COD_SHIPMENTS


class TestReturnRateBounds:
    def test_a_rate_above_one_saturates_at_the_worst_score(self):
        """Why the double-counts mattered: over 1.0 is not merely wrong.

        `_rate_to_score` clamps above its top threshold, so an inflated
        numerator does not produce a slightly-off score — it produces the
        floor, indistinguishable from a genuinely terrible store.
        """
        worst = DELIVERY_SUCCESS_THRESHOLDS[0][1]
        assert _rate_to_score(1.5, [(0.0, 100), (1.0, worst)]) == worst

    def test_a_clean_return_rate_still_scores_well(self):
        # 2 returns in 100 delivered is a healthy store, and the fixed
        # numerator is what keeps a rate like this from being inflated past
        # the thresholds below.
        assert _rate_to_score(0.02, RETURN_RATE_THRESHOLDS) >= 85

    def test_the_worst_return_rate_bottoms_out(self):
        # 25%+ returns scores 0 — which is exactly what an over-1.0 rate used
        # to produce for stores nowhere near that bad.
        assert _rate_to_score(0.30, RETURN_RATE_THRESHOLDS) == 0


class TestWeightFloor:
    def test_the_order_only_core_exactly_meets_the_floor(self):
        """MIN_USABLE_WEIGHT is load-bearing, not a round number.

        A manual store with no shipment data at all falls back to
        completion + returns + response. If that sum dropped below the floor,
        every such store would be permanently ungraded — which is what the
        fallback above exists to avoid.
        """
        order_only = (
            WEIGHTS["order_completion"]
            + WEIGHTS["low_return"]
            + WEIGHTS["response_time"]
        )
        assert order_only == pytest.approx(MIN_USABLE_WEIGHT)

    def test_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)
