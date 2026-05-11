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
from datetime import datetime
from zoneinfo import ZoneInfo


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


def _score_payment_method(payment_method: str | None) -> RiskFactor:
    """Score based on payment method risk profile (backend-016).

    Pre-paid methods (paymob/card/wallet/InstaPay) carry near-zero
    delivery risk because the cash is already collected — the delivery
    failure does not also produce a revenue loss. COD is the high-risk
    case the rest of the model exists to manage. ``unknown`` falls in
    the middle so a misconfigured gateway alias doesn't dominate the
    score either way.
    """
    if not payment_method:
        return RiskFactor(
            factor="payment_method",
            score=50.0,
            weight=0.07,
            reason="Payment method not provided",
        )
    pm = payment_method.lower().replace("-", "_").replace(" ", "_")
    cod_aliases = {"cod", "cash_on_delivery", "cash", "manual"}
    prepaid_aliases = {
        "paymob",
        "card",
        "credit_card",
        "wallet",
        "instapay",
        "fawry",
        "kashier",
        "fawaterak",
        "stripe",
    }
    if pm in cod_aliases:
        return RiskFactor(
            factor="payment_method",
            score=80.0,
            weight=0.07,
            reason="COD — collection risk applies",
        )
    if pm in prepaid_aliases:
        return RiskFactor(
            factor="payment_method",
            score=5.0,
            weight=0.07,
            reason=f"Pre-paid via {payment_method} — collection risk eliminated",
        )
    return RiskFactor(
        factor="payment_method",
        score=45.0,
        weight=0.07,
        reason=f"Payment method '{payment_method}' not recognized",
    )


def _score_time_pattern(created_at: datetime | None) -> RiskFactor:
    """Score based on the time-of-day the order was placed.

    MENA merchants report a strong concentration of fraudulent /
    cancelled COD orders in the 1–5 AM Cairo window — bots, idle
    spam orders, and "I'll deal with it tomorrow" placement that
    almost always cancels. We bump the score for that window and
    leave normal hours at a low baseline.
    """
    if created_at is None:
        return RiskFactor(
            factor="time_pattern",
            score=20.0,
            weight=0.05,
            reason="Order timestamp unavailable — using neutral baseline",
        )
    cairo = ZoneInfo("Africa/Cairo")
    if created_at.tzinfo is None:
        from datetime import UTC

        created_at = created_at.replace(tzinfo=UTC)
    cairo_hour = created_at.astimezone(cairo).hour
    if 1 <= cairo_hour < 5:
        return RiskFactor(
            factor="time_pattern",
            score=70.0,
            weight=0.05,
            reason=f"Placed at {cairo_hour:02d}:xx Cairo — late-night risk window",
        )
    return RiskFactor(
        factor="time_pattern",
        score=10.0,
        weight=0.05,
        reason=f"Placed at {cairo_hour:02d}:xx Cairo — normal hours",
    )


# Tag tokens that flag a product as elevated-risk. Match is
# case-insensitive substring on each tag string. Conservative list —
# tags that are *demonstrably* high-RTO in MENA COD studies.
_HIGH_RISK_PRODUCT_TAGS: frozenset[str] = frozenset({
    "electronics",
    "phone",
    "laptop",
    "jewelry",
    "watch",
    "luxury",
    "perfume",
    "high_value",
})


def _score_product_risk(product_tags: list[str] | None) -> RiskFactor:
    """Score based on product-tag-derived risk.

    The merchant tags products in Shopify; we look for tags matching
    the high-risk list (electronics, jewelry, luxury, etc.) which
    correlate with COD fraud + RTO patterns in MENA. When no tag data
    is available the factor returns 0 with a documented reason — being
    honest that it can't apply, rather than smearing every order with a
    middle-of-the-road "neutral" guess.
    """
    if not product_tags:
        return RiskFactor(
            factor="product_risk",
            score=0.0,
            weight=0.05,
            reason="no_tag_data — store has not tagged products",
        )
    matched = []
    for tag in product_tags:
        if not isinstance(tag, str):
            continue
        normalized = tag.strip().lower().replace("-", "_").replace(" ", "_")
        for risky in _HIGH_RISK_PRODUCT_TAGS:
            if risky in normalized:
                matched.append(tag)
                break
    if matched:
        return RiskFactor(
            factor="product_risk",
            score=70.0,
            weight=0.05,
            reason=f"High-risk product tags: {', '.join(matched[:3])}",
        )
    return RiskFactor(
        factor="product_risk",
        score=10.0,
        weight=0.05,
        reason="No high-risk product tags detected",
    )


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
    # Explicit hard clamp — guarantees 0 <= score <= 100 under all inputs
    dampened_score = max(0, min(100, dampened_score))

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
    created_at: datetime | None = None,
    product_tags: list[str] | None = None,
) -> RiskResult:
    """Score an order for risk and return a RiskResult.

    Full 8-factor scoring (backend-016) with weights summing to 1.00:
      - network_reputation: 0.25
      - customer_history:   0.20
      - order_value:        0.15
      - cancellation_rate:  0.13
      - payment_method:     0.07
      - address_quality:    0.05
      - phone_validation:   0.05
      - time_pattern:       0.05
      - product_risk:       0.05

    Callers may still gate on COD up-front; the model handles non-COD
    payment methods correctly via `_score_payment_method` (collection
    risk drops out for prepaid).
    """
    network_factor = RiskFactor(
        factor="network_reputation",
        score=float(network_score),
        weight=0.25,
        reason=f"Network reputation: {network_label}",
    )
    history = _score_customer_history(customer_total_orders, customer_cod_success_rate)
    history = RiskFactor(
        factor=history.factor, score=history.score, weight=0.20, reason=history.reason
    )

    raw_value = _score_order_value(total_cents, avg_order_cents)
    value = RiskFactor(
        factor=raw_value.factor,
        score=raw_value.score,
        weight=0.15,
        reason=raw_value.reason,
    )

    cancel = _score_cancellation_rate(customer_cancellation_rate, customer_total_orders)
    cancel = RiskFactor(
        factor=cancel.factor, score=cancel.score, weight=0.13, reason=cancel.reason
    )

    pm_factor = _score_payment_method(payment_method)

    addr = _score_address_quality(address)
    addr = RiskFactor(
        factor=addr.factor, score=addr.score, weight=0.05, reason=addr.reason
    )

    phone_f = _score_phone_validation(phone)
    phone_f = RiskFactor(
        factor=phone_f.factor, score=phone_f.score, weight=0.05, reason=phone_f.reason
    )

    time_factor = _score_time_pattern(created_at)
    product_factor = _score_product_risk(product_tags)

    factors: list[RiskFactor] = [
        network_factor,
        history,
        value,
        cancel,
        pm_factor,
        addr,
        phone_f,
        time_factor,
        product_factor,
    ]

    weighted_score = sum(f.score * f.weight for f in factors)
    risk_score = int(_clamp(weighted_score))

    return RiskResult(
        risk_score=risk_score,
        risk_level=_risk_level(risk_score),
        suggested_action=_suggested_action(risk_score),
        factors=factors,
    )
