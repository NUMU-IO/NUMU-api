"""Trust-decision computation — the unified ``SCORED`` → outcome edge (Phase C).

Collapses the fragmented decision authorities (the ``_suggested_action`` risk
ladder, the ``ShopifyAppSettings`` thresholds, the native ``cod_trust_service``
block sequence, and the dormant ``should_auto_approve_trusted`` gate) into ONE
deterministic function both surfaces call. Returns the
:class:`~src.core.entities.trust_decision.TrustDecisionState` an order should
move to from ``SCORED``.

Deterministic + pure (constitution Principle IV — no I/O, no ML). The caller
computes the score (Shopify risk engine or native network+location) and the
gate inputs, then asks this function for the decision; the FSM entity records
the transition. This is the single place a due-diligence reviewer can read to
answer "what does the system do at score X?".
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.services.customer_trust_formula import should_auto_approve_trusted
from src.core.entities.trust_decision import TrustDecisionState

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class DecisionInputs:
    """Everything the decision needs, surface-agnostic.

    ``risk_score`` is the CANONICAL axis: 0-100, ascending-bad (higher =
    riskier), matching the Shopify risk engine. The native checkout passes its
    location-adjusted network risk score on the same 0-100 ascending-bad scale,
    so one function serves both surfaces (resolves the inverted-scale finding
    C-2 / P3-1). ``customer_trust`` is ascending-good (0-100) and only gates the
    trusted-buyer auto-approve.
    """

    risk_score: int
    customer_trust: int = 0
    confidence: str = "low"  # network confidence: low | medium | high
    score_type: str = "preliminary"  # preliminary | final

    # Trusted-buyer auto-approve gate — spec 010 FR-002.
    auto_approve_on_trust_enabled: bool = False
    auto_approve_trust_threshold: int = 80
    install_grace_active: bool = True
    manual_approve_count: int = 0

    # Risk ladder thresholds (ascending-bad).
    confirm_threshold: int = 30
    hold_threshold: int = 70
    cancel_threshold: int = 90
    manual_cancel_count: int = 0  # safety gate for auto-cancel

    # Native-checkout block mode.
    block_enabled: bool = False
    block_threshold: int = 70
    min_confidence_to_act: str = "medium"


def decide(inputs: DecisionInputs) -> TrustDecisionState:
    """Map a scored order to the ``TrustDecisionState`` it should move to.

    Precedence — trusted-buyer auto-approve first, then the most-restrictive
    risk action the safety gates permit:

    1. ``AUTO_APPROVED`` — ``should_auto_approve_trusted`` passes. The gate
       already caps at ``risk <= 90``, so a trusted buyer just over a 90 cancel
       threshold still falls through to the ladder rather than auto-approving.
    2. ``CANCELLED`` — ``risk >= cancel_threshold`` AND the score is final AND
       we're past the install grace AND the merchant has >= 5 manual cancels
       (constitution safe-defaults). A preliminary or in-grace high score holds
       instead of cancelling.
    3. ``BLOCKED`` — native checkout block mode, ``risk >= block_threshold``, and
       the network confidence is high enough to act (never block on thin data).
    4. ``HELD`` — ``risk >= hold_threshold``.
    5. ``CONFIRM_PENDING`` — ``risk >= confirm_threshold``.
    6. ``AUTO_APPROVED`` — low risk; no trust required.
    """
    # 1. Trusted-buyer auto-approve (finally wires the dormant gate — P0-2).
    if should_auto_approve_trusted(
        customer_trust=inputs.customer_trust,
        risk_score=inputs.risk_score,
        auto_approve_on_trust_enabled=inputs.auto_approve_on_trust_enabled,
        auto_approve_trust_threshold=inputs.auto_approve_trust_threshold,
        install_grace_active=inputs.install_grace_active,
        manual_approve_count=inputs.manual_approve_count,
    ):
        return TrustDecisionState.AUTO_APPROVED

    # 2. Safety-gated auto-cancel.
    if (
        inputs.risk_score >= inputs.cancel_threshold
        and inputs.score_type == "final"
        and not inputs.install_grace_active
        and inputs.manual_cancel_count >= 5
    ):
        return TrustDecisionState.CANCELLED

    # 3. Native-checkout block (confidence-gated — never block on thin data).
    if (
        inputs.block_enabled
        and inputs.risk_score >= inputs.block_threshold
        and _CONFIDENCE_RANK.get(inputs.confidence, 0)
        >= _CONFIDENCE_RANK.get(inputs.min_confidence_to_act, 1)
    ):
        return TrustDecisionState.BLOCKED

    # 4. Hold for merchant review.
    if inputs.risk_score >= inputs.hold_threshold:
        return TrustDecisionState.HELD

    # 5. Ask the customer to confirm.
    if inputs.risk_score >= inputs.confirm_threshold:
        return TrustDecisionState.CONFIRM_PENDING

    # 6. Low risk — clear it.
    return TrustDecisionState.AUTO_APPROVED


def native_block_equivalent(state: TrustDecisionState) -> bool:
    """Map an FSM decision to the native checkout's binary block/allow outcome.

    The native storefront only blocks-or-allows COD (it has no hold / confirm /
    cancel surface), so for shadow comparison every non-``BLOCKED`` FSM state
    maps to "allow".
    """
    return state == TrustDecisionState.BLOCKED
