"""Capture a NON-PII snapshot of the inputs a FINAL COD score consumed.

Persisted on ``risk_assessments.decision_inputs`` so any future shadow / equivalence
replay (the NUMU Trust Network cutover proof) can reproduce the recorded ``risk_score``
faithfully — *without* re-introducing raw PII (constitution Principle II: raw phone /
address are never stored on this row).

The trick: every risk factor is a deterministic function of its input, and the two
PII-derived factors depend on their input only through a non-PII determinant:

- ``address_quality`` depends solely on ``len(address.strip())``  → store ``address_length``
- ``phone_validation`` depends solely on absent / valid-Egyptian / invalid
  → store ``phone_state`` (the exact branch the engine takes)

So this snapshot fully determines all nine factors with zero PII. ``_phone_state``
imports the engine's own regex + cleaning so the captured determinant can never drift
from what the scorer actually computed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.application.use_cases.shopify.risk_scoring_engine import _EGYPTIAN_MOBILE_RE

# Bump when the captured shape changes so replayers can branch on it.
DECISION_INPUTS_SCHEMA_VERSION = 1


def _phone_state(phone: str | None) -> str:
    """absent / valid / invalid — the exact branch ``_score_phone_validation`` takes."""
    if not phone:
        return "absent"
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    return "valid" if _EGYPTIAN_MOBILE_RE.match(cleaned) else "invalid"


def build_decision_inputs(
    *,
    total_cents: int,
    payment_method: str | None,
    customer_total_orders: int,
    customer_cancellation_rate: float | None,
    avg_order_cents: int,
    network_score: int | None,
    network_label: str | None,
    created_at: datetime | None,
    product_tags: list[str] | None,
    address: str | None,
    phone: str | None,
) -> dict[str, Any]:
    """Build the non-PII input snapshot for the final-score write path.

    Mirrors exactly what ``score_order`` was called with; PII (``address``/``phone``)
    is reduced to its score determinant and never stored raw.
    """
    return {
        "schema_version": DECISION_INPUTS_SCHEMA_VERSION,
        "total_cents": total_cents,
        "payment_method": payment_method,
        "customer_total_orders": customer_total_orders,
        "customer_cancellation_rate": customer_cancellation_rate,
        "avg_order_cents": avg_order_cents,
        "network_score": network_score,
        "network_label": network_label,
        "created_at": created_at.isoformat() if created_at else None,
        "product_tags": list(product_tags) if product_tags else [],
        # PII-derived determinants only — never the raw address/phone.
        "address_length": len(address.strip()) if address else None,
        "phone_state": _phone_state(phone),
    }
