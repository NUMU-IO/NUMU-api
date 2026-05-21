"""Deterministic ``customer_trust`` factor for the risk-scoring pipeline.

Implements the signed-formula from spec 010 FR-001 + CL-003: positive
contribution from successful deliveries / prepaid orders / WhatsApp
responsiveness / network-positive events MINUS a negative adjustment
from network-RTOs / local recent refusals / local lifetime refusals.

Scoring is purely deterministic per constitution Principle IV — no ML.
The Shopify-app-side spec 010 visualises the resulting tier (none / new
/ bronze / silver / gold) and exposes an auto-approve toggle gated on
``customer_trust >= auto_approve_trust_threshold``.

The formula is documented as a worked-example in
``docs/risk-scoring/customer-trust-formula.md`` (out of scope here).
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Formula constants (spec 010 CL-003 + FR-001)
# ---------------------------------------------------------------------------

TRUST_WEIGHT_SUCCESSFUL_DELIVERIES = 4
TRUST_WEIGHT_PREPAID_ORDERS = 6
TRUST_WEIGHT_WA_RESPONSE_RATE = 0.1  # Per pct point
TRUST_WEIGHT_NETWORK_POSITIVE = 3

TRUST_PENALTY_NETWORK_NEGATIVE = 8
TRUST_PENALTY_LOCAL_RECENT_REFUSAL = 6
TRUST_PENALTY_LOCAL_LIFETIME_REFUSAL = 2

# Tier-band boundaries — the Shopify-app side derives the badge from these.
TRUST_TIER_NEW_MIN = 1
TRUST_TIER_BRONZE_MIN = 30
TRUST_TIER_SILVER_MIN = 60
TRUST_TIER_GOLD_MIN = 80

# Auto-approve cap — even a Gold customer with very high risk still goes
# to manual review (spec 010 FR-002 risk_score > 90 cap).
AUTO_APPROVE_RISK_CAP = 90

# Kill-switch params (spec 010 CL-002 — maintainer-confirmed).
AUTO_APPROVE_KILL_SWITCH_MIN_SAMPLE = 20
AUTO_APPROVE_KILL_SWITCH_MAX_RTO_RATE = 0.05


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass
class TrustInputs:
    """All counters the formula consumes — none of them are PII."""

    successful_deliveries: int = 0
    prepaid_orders: int = 0
    whatsapp_response_rate_pct: float = 0.0  # 0-100
    network_positive_events: int = 0
    network_negative_events: int = 0
    local_recent_refusals: int = 0
    local_lifetime_refusals: int = 0


@dataclass
class TrustResult:
    """Output of the formula for a single ``RiskAssessment``.

    ``negative_adjustment_count`` is exposed so the Shopify-app tooltip
    can render "(adjusted for {N} prior refusals)" per spec 010 CL-003.
    ``trust_lookup_degraded`` is set when network signal lookup failed
    and the score fell back to local-only inputs (spec 010 US1 AS-4).
    """

    customer_trust: int  # 0-100
    trust_tier: str  # 'none' | 'new' | 'bronze' | 'silver' | 'gold'
    negative_adjustment_count: int
    trust_lookup_degraded: bool = False


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _tier_for(score: int, has_history: bool) -> str:
    """Map a numeric trust score to the Shopify-app badge tier."""
    if score >= TRUST_TIER_GOLD_MIN:
        return "gold"
    if score >= TRUST_TIER_SILVER_MIN:
        return "silver"
    if score >= TRUST_TIER_BRONZE_MIN:
        return "bronze"
    if score >= TRUST_TIER_NEW_MIN:
        return "new"
    # 0 with no positive history at all → no badge.
    return "new" if has_history else "none"


def compute_customer_trust(
    inputs: TrustInputs,
    *,
    trust_lookup_degraded: bool = False,
) -> TrustResult:
    """Deterministic trust-score computation per spec 010 FR-001.

    The signed formula MUST stay reproducible from the inputs alone —
    snapshot tests pin the output for ~20 representative profiles per
    spec 010 SC-001 (constitution Principle IV).
    """
    positive = (
        inputs.successful_deliveries * TRUST_WEIGHT_SUCCESSFUL_DELIVERIES
        + inputs.prepaid_orders * TRUST_WEIGHT_PREPAID_ORDERS
        + inputs.whatsapp_response_rate_pct * TRUST_WEIGHT_WA_RESPONSE_RATE
        + inputs.network_positive_events * TRUST_WEIGHT_NETWORK_POSITIVE
    )
    negative_adjustment = (
        inputs.network_negative_events * TRUST_PENALTY_NETWORK_NEGATIVE
        + inputs.local_recent_refusals * TRUST_PENALTY_LOCAL_RECENT_REFUSAL
        + inputs.local_lifetime_refusals * TRUST_PENALTY_LOCAL_LIFETIME_REFUSAL
    )
    score = int(_clamp(positive - negative_adjustment))

    has_history = (
        inputs.successful_deliveries
        or inputs.prepaid_orders
        or inputs.network_positive_events
        or inputs.network_negative_events
        or inputs.local_recent_refusals
        or inputs.local_lifetime_refusals
    ) > 0

    return TrustResult(
        customer_trust=score,
        trust_tier=_tier_for(score, has_history),
        negative_adjustment_count=(
            inputs.network_negative_events
            + inputs.local_recent_refusals
            + inputs.local_lifetime_refusals
        ),
        trust_lookup_degraded=trust_lookup_degraded,
    )


def should_auto_approve_trusted(
    *,
    customer_trust: int,
    risk_score: int,
    auto_approve_on_trust_enabled: bool,
    auto_approve_trust_threshold: int,
    install_grace_active: bool,
    manual_approve_count: int,
) -> bool:
    """Encode the spec 010 FR-002 + CL-001 + CL-004 auto-approve gating.

    Returns True iff every precondition is met:

    * merchant has the toggle on
    * the trust score crosses the merchant-configured threshold
    * the risk score is below the hard 90-cap (spec 010 FR-002)
    * we're past the 30-day install grace window (constitution v1.1.0)
    * the merchant has manually approved at least 5 risky orders
      (spec 010 CL-001 — counting approves, not cancels)
    """
    if not auto_approve_on_trust_enabled:
        return False
    if customer_trust < auto_approve_trust_threshold:
        return False
    if risk_score > AUTO_APPROVE_RISK_CAP:
        return False
    if install_grace_active:
        return False
    if manual_approve_count < 5:
        return False
    return True


def kill_switch_should_disable(
    *,
    auto_approve_count: int,
    rto_count: int,
) -> bool:
    """Spec 010 CL-002: dormant below ≥20 sample; trips above 5% RTO rate."""
    if auto_approve_count < AUTO_APPROVE_KILL_SWITCH_MIN_SAMPLE:
        return False
    if auto_approve_count == 0:
        return False
    return (rto_count / auto_approve_count) > AUTO_APPROVE_KILL_SWITCH_MAX_RTO_RATE
