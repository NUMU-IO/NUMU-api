"""Unit tests for the network reputation scoring engine.

Covers `compute_network_score` — the dampened, confidence-aware function
that turns raw counts (orders / RTOs / refunds / deliveries) into a
0–100 score, confidence label, and human-readable category.
"""

from __future__ import annotations

import pytest

from src.application.use_cases.shopify.risk_scoring_engine import (
    compute_network_score,
)

# ─── Edge case: no orders ─────────────────────────────────────────────


def test_no_orders_returns_baseline():
    score, conf, label = compute_network_score(
        total_orders=0,
        total_rtos=0,
        total_deliveries=0,
        total_refunds=0,
        contributing_store_count=0,
    )
    assert score == 55
    assert conf == "low"
    assert label == "new_to_network"


# ─── Confidence buckets ───────────────────────────────────────────────


def test_two_orders_yields_low_confidence():
    _, conf, _ = compute_network_score(
        total_orders=2,
        total_rtos=0,
        total_deliveries=2,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert conf == "low"


def test_three_orders_yields_medium_confidence():
    _, conf, _ = compute_network_score(
        total_orders=3,
        total_rtos=0,
        total_deliveries=3,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert conf == "medium"


def test_ten_orders_yields_high_confidence():
    _, conf, _ = compute_network_score(
        total_orders=10,
        total_rtos=0,
        total_deliveries=10,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert conf == "high"


# ─── Score boundaries ─────────────────────────────────────────────────


def test_all_rto_does_not_exceed_100():
    score, _, _ = compute_network_score(
        total_orders=10,
        total_rtos=10,
        total_deliveries=0,
        total_refunds=10,
        contributing_store_count=5,
    )
    assert 0 <= score <= 100


def test_perfect_record_at_high_confidence_is_low_score():
    score, _, label = compute_network_score(
        total_orders=10,
        total_rtos=0,
        total_deliveries=10,
        total_refunds=0,
        contributing_store_count=2,
    )
    # 0% RTO with 7 deliveries beyond first 3 = -15 = 0 → trusted
    assert score == 0
    assert label.startswith("trusted_buyer")


def test_delivery_bonus_capped_at_minus_15():
    """20 deliveries shouldn't pull the score below the floor."""
    score, _, _ = compute_network_score(
        total_orders=20,
        total_rtos=0,
        total_deliveries=20,
        total_refunds=0,
        contributing_store_count=1,
    )
    # Raw score is 0 (no RTOs), bonus cap means it can't go negative.
    assert score == 0


def test_refund_penalty_capped_at_plus_20():
    score_one, _, _ = compute_network_score(
        total_orders=10,
        total_rtos=0,
        total_deliveries=0,
        total_refunds=2,  # +20 max
        contributing_store_count=1,
    )
    score_many, _, _ = compute_network_score(
        total_orders=10,
        total_rtos=0,
        total_deliveries=0,
        total_refunds=10,  # would be +100 if uncapped
        contributing_store_count=1,
    )
    assert score_one == score_many  # both capped at +20


# ─── Confidence dampening ─────────────────────────────────────────────


def test_dampening_at_five_orders_blends_to_baseline():
    """At 5 orders, confidence_factor=0.5 → score is halfway between
    baseline (55) and raw_score (100 for all-RTO)."""
    score, _, _ = compute_network_score(
        total_orders=5,
        total_rtos=5,
        total_deliveries=0,
        total_refunds=0,
        contributing_store_count=1,
    )
    # raw=100, baseline=55, factor=0.5 → 77 or 78
    assert 75 <= score <= 80


def test_dampening_at_ten_orders_uses_raw_score():
    """At 10+ orders the dampening factor maxes at 1.0 — no blending."""
    score, _, _ = compute_network_score(
        total_orders=10,
        total_rtos=10,
        total_deliveries=0,
        total_refunds=0,
        contributing_store_count=1,
    )
    # 100% RTO + +20 refund cap = 100 (clamped) + 0 deliveries = 100
    assert score == 100


# ─── Label categorization ─────────────────────────────────────────────


def test_label_categories():
    """Score buckets: 0-25 trusted, 26-55 neutral, 56-75 risky, 76+ serial."""
    # Drive specific known scores by tuning total_orders and rtos.
    # 10 orders, 0 RTO → score 0 → trusted_buyer
    _, _, trusted_label = compute_network_score(
        total_orders=10,
        total_rtos=0,
        total_deliveries=0,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert trusted_label.startswith("trusted_buyer")

    # 5 orders, 5 RTO → raw 100, dampened to ~77 → serial_abuser
    _, _, serial_label = compute_network_score(
        total_orders=5,
        total_rtos=5,
        total_deliveries=0,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert serial_label.startswith("serial_abuser")

    # 10 orders, 7 RTO → raw 70, dampened 70 → risky
    _, _, risky_label = compute_network_score(
        total_orders=10,
        total_rtos=7,
        total_deliveries=3,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert risky_label.startswith("risky")

    # 10 orders, 5 RTO → raw 50, dampened 50 → neutral
    _, _, neutral_label = compute_network_score(
        total_orders=10,
        total_rtos=5,
        total_deliveries=5,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert neutral_label.startswith("neutral")


def test_label_includes_store_breadth_when_multiple():
    _, _, label = compute_network_score(
        total_orders=10,
        total_rtos=10,
        total_deliveries=0,
        total_refunds=0,
        contributing_store_count=4,
    )
    assert "(4 stores)" in label


def test_label_no_breadth_at_single_store():
    _, _, label = compute_network_score(
        total_orders=10,
        total_rtos=10,
        total_deliveries=0,
        total_refunds=0,
        contributing_store_count=1,
    )
    assert "stores" not in label


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_score(*, target: int) -> tuple[int, str, str]:
    """Coax compute_network_score into producing approximately `target`.

    Used so we can drive label-bucket tests without manually hunting for
    input combos. Brute-forces with 10 orders + tunable RTO ratio.
    """
    rtos = round(target / 10)
    rtos = max(0, min(10, rtos))
    deliveries = 10 - rtos
    return compute_network_score(
        total_orders=10,
        total_rtos=rtos,
        total_deliveries=deliveries,
        total_refunds=0,
        contributing_store_count=1,
    )


@pytest.mark.parametrize(
    "orders,rtos,refunds,expected_min,expected_max",
    [
        # 0 orders → baseline 55
        (0, 0, 0, 55, 55),
        # 5 orders, 0 RTO → blended toward baseline
        (5, 0, 0, 25, 35),
        # 10 orders, 0 RTO → 0 (raw rate 0)
        (10, 0, 0, 0, 5),
    ],
)
def test_score_ranges(orders, rtos, refunds, expected_min, expected_max):
    score, _, _ = compute_network_score(
        total_orders=orders,
        total_rtos=rtos,
        total_deliveries=orders - rtos,
        total_refunds=refunds,
        contributing_store_count=1,
    )
    assert expected_min <= score <= expected_max
