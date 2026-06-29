"""Unit tests for the non-PII decision-input snapshot (Trust Network replay capture)."""

from datetime import UTC, datetime

from src.application.services.decision_input_capture import (
    DECISION_INPUTS_SCHEMA_VERSION,
    _phone_state,
    build_decision_inputs,
)


def test_phone_state_branches_match_engine():
    assert _phone_state(None) == "absent"
    assert _phone_state("") == "absent"
    # The engine regex requires the 20 country code (optionally +-prefixed).
    assert _phone_state("+201012345678") == "valid"
    assert _phone_state("201112345678") == "valid"
    assert _phone_state("+20 101 234 5678") == "valid"  # spaces are cleaned
    # Local 0-prefixed form is NOT matched by the engine → invalid (faithful capture).
    assert _phone_state("01012345678") == "invalid"
    assert _phone_state("not-a-phone") == "invalid"


def test_build_decision_inputs_captures_determinants_without_pii():
    created = datetime(2026, 6, 29, 21, 30, tzinfo=UTC)
    out = build_decision_inputs(
        total_cents=40000,
        payment_method="cod",
        customer_total_orders=3,
        customer_cancellation_rate=0.1,
        avg_order_cents=80000,
        network_score=55,
        network_label="moderate",
        created_at=created,
        product_tags=["electronics"],
        address="12 Tahrir Street, Cairo, Egypt",
        phone="+201012345678",
    )
    assert out["schema_version"] == DECISION_INPUTS_SCHEMA_VERSION
    assert out["total_cents"] == 40000
    assert out["network_score"] == 55
    assert out["address_length"] == len("12 Tahrir Street, Cairo, Egypt")
    assert out["phone_state"] == "valid"
    assert out["created_at"] == created.isoformat()
    assert out["product_tags"] == ["electronics"]
    # The raw PII must never appear anywhere in the snapshot.
    blob = repr(out)
    assert "Tahrir" not in blob
    assert "201012345678" not in blob


def test_build_decision_inputs_handles_missing_address_and_phone():
    out = build_decision_inputs(
        total_cents=0,
        payment_method=None,
        customer_total_orders=0,
        customer_cancellation_rate=None,
        avg_order_cents=80000,
        network_score=None,
        network_label=None,
        created_at=None,
        product_tags=None,
        address=None,
        phone=None,
    )
    assert out["address_length"] is None
    assert out["phone_state"] == "absent"
    assert out["created_at"] is None
    assert out["product_tags"] == []
