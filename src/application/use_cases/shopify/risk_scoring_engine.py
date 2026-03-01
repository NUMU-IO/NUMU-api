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
from typing import Any


@dataclass
class RiskFactor:
    factor: str
    score: float          # 0-100
    weight: float
    reason: str


@dataclass
class RiskResult:
    risk_score: int       # 0-100
    risk_level: str       # low | medium | high | critical
    suggested_action: str # auto_approve | whatsapp_confirm | hold | cancel
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
    return RiskFactor(factor="customer_history", score=score, weight=0.35, reason=reason)


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


# ── main entry point ────────────────────────────────────────

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
) -> RiskResult:
    """Score an order for risk and return a RiskResult.

    Only COD orders should be scored — callers should check payment_method
    before invoking.
    """
    factors: list[RiskFactor] = [
        _score_customer_history(customer_total_orders, customer_cod_success_rate),
        _score_order_value(total_cents, avg_order_cents),
        _score_cancellation_rate(customer_cancellation_rate, customer_total_orders),
        _score_address_quality(address),
        _score_phone_validation(phone),
    ]

    weighted_score = sum(f.score * f.weight for f in factors)
    risk_score = int(_clamp(weighted_score))

    return RiskResult(
        risk_score=risk_score,
        risk_level=_risk_level(risk_score),
        suggested_action=_suggested_action(risk_score),
        factors=factors,
    )
