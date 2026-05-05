"""Unit tests for the InstaPay auto-approval rules engine.

The rules engine is a pure function over plain dataclasses, so these
tests deliberately avoid any DB / async infrastructure — one decision
per case, no fixtures beyond the small helpers below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.core.entities.instapay import (
    InstapayIntent,
    InstapayIntentStatus,
    PaymentProof,
)
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
    AutoApprovalFacts,
    evaluate,
)


def _intent(expires_in_min: int = 30) -> InstapayIntent:
    now = datetime.now(UTC)
    return InstapayIntent(
        id=uuid4(),
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        reference_code="NU-TESTXX",
        display_ipa="merchant@cib",
        amount_cents=10_000,
        expires_at=now + timedelta(minutes=expires_in_min),
        qr_payload="instapay://pay?...",
        status=InstapayIntentStatus.AWAITING_PAYMENT,
    )


def _proof(declared_amount_cents: int | None = None) -> PaymentProof:
    return PaymentProof.new(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        proof_image_key="key",
        proof_image_hash=b"\x00" * 32,
        transaction_ref="BANK-REF-1",
        declared_amount_cents=declared_amount_cents,
    )


def _config(**overrides) -> AutoApprovalConfig:
    defaults = {
        "threshold_cents": 50_000,
        "daily_cap_cents": 500_000,
        "daily_count_cap": 10,
    }
    defaults.update(overrides)
    return AutoApprovalConfig(**defaults)


def _facts(**overrides) -> AutoApprovalFacts:
    defaults = {
        "order_total_cents": 10_000,
        "daily_auto_approved_count": 0,
        "daily_auto_approved_cents": 0,
    }
    defaults.update(overrides)
    return AutoApprovalFacts(**defaults)


class TestAutoApprovalHappyPath:
    """No rule trips → auto-approve."""

    def test_under_threshold_and_under_caps_auto_approves(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(),
            config=_config(),
            facts=_facts(),
        )
        assert d.approved is True
        assert d.reasons == []
        assert d.soft_block is False


class TestAutoApprovalHardBlock:
    """Intent expiry is the only hard block — customer must restart."""

    def test_expired_intent_hard_blocks(self):
        d = evaluate(
            intent=_intent(expires_in_min=-5),
            proof=_proof(),
            config=_config(),
            facts=_facts(),
        )
        assert d.approved is False
        assert "intent_expired" in d.reasons
        assert d.soft_block is False


class TestAutoApprovalSoftBlocks:
    """Any soft block sends the proof to merchant review rather than rejecting.

    All of these should preserve ``soft_block=True`` so the use case
    routes to AWAITING_REVIEW rather than failing the upload outright.
    Dedup (image-hash / transaction-ref) is deliberately *not* in this
    list — the use case short-circuits duplicates with a 409 before
    ever calling evaluate(), so the engine stays dedup-agnostic.
    """

    def test_amount_above_threshold_routes_to_review(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(),
            config=_config(threshold_cents=10_000),
            facts=_facts(order_total_cents=20_000),
        )
        assert d.approved is False
        assert d.soft_block is True
        assert "amount_above_auto_approve_threshold" in d.reasons

    def test_daily_count_exceeded_routes_to_review(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(),
            config=_config(daily_count_cap=5),
            facts=_facts(daily_auto_approved_count=5),
        )
        assert d.approved is False
        assert "daily_auto_approve_count_exceeded" in d.reasons

    def test_daily_cents_exceeded_routes_to_review(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(),
            config=_config(daily_cap_cents=15_000),
            facts=_facts(
                order_total_cents=10_000,
                daily_auto_approved_cents=10_000,
            ),
        )
        assert d.approved is False
        assert "daily_auto_approve_amount_exceeded" in d.reasons


class TestAmountMismatchRule:
    """The #12 rule — declared amount off by more than N bps goes to review."""

    def test_exact_declared_match_auto_approves(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(declared_amount_cents=10_000),
            config=_config(amount_mismatch_tolerance_bps=100),
            facts=_facts(order_total_cents=10_000),
        )
        assert d.approved is True

    def test_within_one_percent_auto_approves(self):
        # 0.5% below the order total — within default 1% tolerance
        d = evaluate(
            intent=_intent(),
            proof=_proof(declared_amount_cents=9_950),
            config=_config(amount_mismatch_tolerance_bps=100),
            facts=_facts(order_total_cents=10_000),
        )
        assert d.approved is True

    def test_five_percent_below_routes_to_review(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(declared_amount_cents=9_500),
            config=_config(amount_mismatch_tolerance_bps=100),
            facts=_facts(order_total_cents=10_000),
        )
        assert d.approved is False
        assert "declared_amount_mismatch" in d.reasons
        assert d.soft_block is True

    def test_disabled_tolerance_skips_check(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(declared_amount_cents=1),  # absurdly wrong
            config=_config(amount_mismatch_tolerance_bps=None),
            facts=_facts(order_total_cents=10_000),
        )
        assert d.approved is True
        assert "declared_amount_mismatch" not in d.reasons

    def test_missing_declared_amount_skips_check(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(declared_amount_cents=None),
            config=_config(amount_mismatch_tolerance_bps=100),
            facts=_facts(order_total_cents=10_000),
        )
        assert d.approved is True


class TestMultipleReasons:
    """All failing rules must be returned — not just the first found."""

    def test_all_failing_rules_listed(self):
        d = evaluate(
            intent=_intent(),
            proof=_proof(declared_amount_cents=1_000),  # 90% off
            config=_config(
                threshold_cents=5_000,
                daily_count_cap=1,
                amount_mismatch_tolerance_bps=100,
            ),
            facts=_facts(
                order_total_cents=10_000,
                daily_auto_approved_count=1,
                daily_auto_approved_cents=100_000,
            ),
        )
        assert d.approved is False
        assert "amount_above_auto_approve_threshold" in d.reasons
        assert "declared_amount_mismatch" in d.reasons
        assert "daily_auto_approve_count_exceeded" in d.reasons
        assert d.soft_block is True  # no hard block among these
