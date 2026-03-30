"""Risk scoring engine for COD orders.

Scores orders on a 0–100 scale using weighted factors:
  - customer_history  (0.35)
  - order_value       (0.20)
  - cancellation_rate (0.20)
  - address_quality   (0.15)
  - phone_validation  (0.10)

The engine is stateless — it receives order data and returns a score with
factor breakdowns.  In production you would enrich from the DB; for now it
derives heuristic scores from the order payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RiskFactor:
    factor: str
    score: float  # 0-100
    weight: float
    reason: str


@dataclass
class RiskResult:
    risk_score: int  # 0-100
    risk_level: str  # low | medium | high | critical
    suggested_action: str  # auto_approve | whatsapp_confirm | hold | cancel
    factors: list[RiskFactor] = field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────

_EGYPTIAN_MOBILE_RE = re.compile(r"^\+?20(10|11|12|15)\d{8}$")


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _risk_level(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _suggested_action(score: int) -> str:
    if score >= 90:
        return "cancel"
    if score >= 70:
        return "hold"
    if score >= 30:
        return "whatsapp_confirm"
    return "auto_approve"


# ── factor scorers ───────────────────────────────────────────


def _score_customer_history(
    total_orders: int,
    cod_success_rate: float | None,
) -> RiskFactor:
    """Score based on how many past orders the customer has and COD success."""
    if total_orders == 0:
        score = 75.0
        reason = "First-time customer — no purchase history available"
    elif total_orders < 3:
        score = 50.0
        reason = f"{total_orders} previous order(s) — limited history"
    else:
        rate = cod_success_rate if cod_success_rate is not None else 1.0
        score = _clamp(100 - rate * 100)
        reason = (
            f"Returning customer — {total_orders} orders, "
            f"{int(rate * 100)}% COD success rate"
        )
    return RiskFactor(
        factor="customer_history", score=score, weight=0.35, reason=reason
    )


def _score_order_value(
    total_cents: int,
    avg_order_cents: int = 80_000,  # 800 EGP
) -> RiskFactor:
    """Score based on how the order total compares to the store average."""
    if avg_order_cents <= 0:
        avg_order_cents = 80_000
    ratio = total_cents / avg_order_cents
    if ratio <= 1.0:
        score = 10.0
        label = "Normal"
    elif ratio <= 2.0:
        score = 40.0
        label = "Above-average"
    elif ratio <= 3.5:
        score = 70.0
        label = "High"
    else:
        score = 90.0
        label = "Very high"
    egp = total_cents / 100
    reason = f"{label} order value: {egp:,.0f} EGP"
    return RiskFactor(factor="order_value", score=score, weight=0.20, reason=reason)


def _score_cancellation_rate(
    cancellation_rate: float | None,
    total_orders: int,
) -> RiskFactor:
    """Score based on customer's historical cancellation rate."""
    if total_orders == 0 or cancellation_rate is None:
        return RiskFactor(
            factor="cancellation_rate",
            score=40.0,
            weight=0.20,
            reason="No cancellation history available",
        )
    pct = int(cancellation_rate * 100)
    if cancellation_rate <= 0.1:
        score = 5.0
    elif cancellation_rate <= 0.3:
        score = 40.0
    elif cancellation_rate <= 0.6:
        score = 70.0
    else:
        score = 95.0
    return RiskFactor(
        factor="cancellation_rate",
        score=score,
        weight=0.20,
        reason=f"{'Low' if pct <= 10 else 'Elevated' if pct <= 50 else 'Very high'} cancellation rate: {pct}%",
    )


def _score_address_quality(address: str | None) -> RiskFactor:
    """Score based on address completeness."""
    if not address:
        return RiskFactor(
            factor="address_quality",
            score=70.0,
            weight=0.15,
            reason="No address provided",
        )
    length = len(address.strip())
    if length < 10:
        score = 60.0
        reason = "Address suspiciously short"
    elif length < 30:
        score = 30.0
        reason = "Address appears incomplete"
    else:
        score = 8.0
        reason = "Address appears complete and valid"
    return RiskFactor(factor="address_quality", score=score, weight=0.15, reason=reason)


def _score_phone_validation(phone: str | None) -> RiskFactor:
    """Score based on phone number format."""
    if not phone:
        return RiskFactor(
            factor="phone_validation",
            score=60.0,
            weight=0.10,
            reason="No phone number provided",
        )
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    if _EGYPTIAN_MOBILE_RE.match(cleaned):
        return RiskFactor(
            factor="phone_validation",
            score=10.0,
            weight=0.10,
            reason="Valid Egyptian mobile number",
        )
    return RiskFactor(
        factor="phone_validation",
        score=50.0,
        weight=0.10,
        reason="Phone number format could not be verified",
    )


# ── network reputation scoring ─────────────────────────────

_NEW_TO_NETWORK_BASELINE = 55


def compute_network_score(
    *,
    total_orders: int,
    total_rtos: int,
    total_deliveries: int,
    total_refunds: int,
    contributing_store_count: int,
) -> tuple[int, str, str]:
    """Compute a network reputation score with confidence dampening.

    Returns ``(score, confidence_level, label)``.

    **Formula**:
    - ``raw_rto_rate`` = RTOs / orders (if orders > 0).
    - ``raw_score`` = ``raw_rto_rate * 100`` (0 = perfect, 100 = all RTOs).
    - Refunds add a penalty: ``+10`` per refund (capped at +20).
    - Successful deliveries dampen the score: ``-5`` per delivery beyond
      the first 3 (capped at -15).

    **Confidence dampening** pulls the score toward the baseline when data
    is scarce.  The dampening factor is ``min(total_orders / 10, 1.0)`` —
    at 10+ orders the raw score is fully trusted; below that it blends
    toward the baseline (55).

    **Confidence levels**:
    - ``low``: 0–2 orders
    - ``medium``: 3–9 orders
    - ``high``: 10+ orders
    """
    if total_orders == 0:
        return _NEW_TO_NETWORK_BASELINE, "low", "new_to_network"

    # Raw RTO-based score
    raw_rto_rate = total_rtos / total_orders
    raw_score = raw_rto_rate * 100

    # Refund penalty (+10 per refund, max +20)
    raw_score += min(total_refunds * 10, 20)

    # Delivery bonus (-5 per delivery beyond 3, max -15)
    if total_deliveries > 3:
        raw_score -= min((total_deliveries - 3) * 5, 15)

    raw_score = _clamp(raw_score)

    # Confidence dampening: blend toward baseline when data is sparse
    confidence_factor = min(total_orders / 10.0, 1.0)
    dampened_score = int(
        _NEW_TO_NETWORK_BASELINE * (1 - confidence_factor)
        + raw_score * confidence_factor
    )
    dampened_score = int(_clamp(dampened_score))

    # Confidence level
    if total_orders >= 10:
        confidence = "high"
    elif total_orders >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    # Label
    if dampened_score <= 25:
        label = "trusted_buyer"
    elif dampened_score <= 55:
        label = "neutral"
    elif dampened_score <= 75:
        label = "risky"
    else:
        label = "serial_abuser"

    # Include store breadth in label
    if contributing_store_count > 1:
        label += f" ({contributing_store_count} stores)"

    return dampened_score, confidence, label


# ── main entry points ───────────────────────────────────────


def score_order_fast(
    *,
    total_cents: int,
    avg_order_cents: int = 80_000,
    network_score: int = 55,
    network_label: str = "new_to_network",
) -> RiskResult:
    """Compute a synchronous 2-factor fast risk score (<200ms).

    Uses only:
    - ``network_reputation`` — cross-merchant buyer reputation score.
    - ``order_value`` — order total vs the store's rolling Redis average.

    Returns a RiskResult with ``score_type`` implied as ``"preliminary"``.
    The Celery task will upgrade to a full 5-factor ``"final"`` score.
    """
    reason = (
        f"Network reputation: {network_label}"
        if network_label != "new_to_network"
        else f"Network reputation: {network_label} — baseline score applied"
    )
    network_factor = RiskFactor(
        factor="network_reputation",
        score=float(network_score),
        weight=0.60,
        reason=reason,
    )
    raw_value_factor = _score_order_value(total_cents, avg_order_cents)
    value_factor = RiskFactor(
        factor=raw_value_factor.factor,
        score=raw_value_factor.score,
        weight=0.40,
        reason=raw_value_factor.reason,
    )

    factors = [network_factor, value_factor]
    weighted_score = sum(f.score * f.weight for f in factors)
    risk_score = int(_clamp(weighted_score))

    return RiskResult(
        risk_score=risk_score,
        risk_level=_risk_level(risk_score),
        suggested_action=_suggested_action(risk_score),
        factors=factors,
    )


def score_order(
    *,
    total_cents: int,
    payment_method: str | None = None,
    customer_total_orders: int = 0,
    customer_cod_success_rate: float | None = None,
    customer_cancellation_rate: float | None = None,
    address: str | None = None,
    phone: str | None = None,
    avg_order_cents: int = 80_000,
    network_score: int = 55,
    network_label: str = "new_to_network",
) -> RiskResult:
    """Score an order for risk and return a RiskResult.

    Full 5-factor scoring with weights:
    - network_reputation: 0.30
    - customer_history:   0.25
    - order_value:        0.20
    - cancellation_rate:  0.15
    - address_quality:    0.05
    - phone_validation:   0.05

    Only COD orders should be scored — callers should check payment_method
    before invoking.
    """
    network_factor = RiskFactor(
        factor="network_reputation",
        score=float(network_score),
        weight=0.30,
        reason=f"Network reputation: {network_label}",
    )
    history = _score_customer_history(customer_total_orders, customer_cod_success_rate)
    history = RiskFactor(
        factor=history.factor, score=history.score, weight=0.25, reason=history.reason
    )

    value = _score_order_value(total_cents, avg_order_cents)

    cancel = _score_cancellation_rate(customer_cancellation_rate, customer_total_orders)
    cancel = RiskFactor(
        factor=cancel.factor, score=cancel.score, weight=0.15, reason=cancel.reason
    )

    addr = _score_address_quality(address)
    addr = RiskFactor(
        factor=addr.factor, score=addr.score, weight=0.05, reason=addr.reason
    )

    phone_f = _score_phone_validation(phone)
    phone_f = RiskFactor(
        factor=phone_f.factor, score=phone_f.score, weight=0.05, reason=phone_f.reason
    )

    factors: list[RiskFactor] = [network_factor, history, value, cancel, addr, phone_f]

    weighted_score = sum(f.score * f.weight for f in factors)
    risk_score = int(_clamp(weighted_score))

    return RiskResult(
        risk_score=risk_score,
        risk_level=_risk_level(risk_score),
        suggested_action=_suggested_action(risk_score),
        factors=factors,
    )
