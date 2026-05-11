"""Dedup-key construction for Shopify Flow trigger emissions (backend-020).

Each trigger handle has its own dedup key shape per backend-020 FR-003.
Centralised here so emission writes + idempotency lookups agree on the
exact key format.
"""

from __future__ import annotations

from datetime import date

# Trigger handle constants — must match the schemas spec 004 registers
# with Shopify in `extensions/flow-triggers/`.
TRIGGER_RISK_SCORE_CALCULATED = "risk_score_calculated"
TRIGGER_COD_VERIFICATION_COMPLETED = "cod_verification_completed"
TRIGGER_NETWORK_SIGNAL_THRESHOLD_CROSSED = "network_signal_threshold_crossed"
TRIGGER_RECOVERY_SUCCEEDED = "recovery_succeeded"
TRIGGER_RECOVERY_ABANDONED = "recovery_abandoned"


def dedup_key_risk_score_calculated(shopify_order_id: str, score_type: str) -> str:
    """One trigger per (order, preliminary|final) — accommodates hybrid sync/async."""
    return f"{shopify_order_id}:{score_type}"


def dedup_key_cod_verification(shopify_order_id: str, transition_id: str) -> str:
    """Allows multiple verifications if the customer flips state."""
    return f"{shopify_order_id}:verification:{transition_id}"


def dedup_key_network_threshold(
    customer_phone_hash: str, threshold: int, period_start: date
) -> str:
    """Natural per-period dedup using the merchant-config rolling window start."""
    return f"{customer_phone_hash}:{threshold}:{period_start.isoformat()}"


def dedup_key_recovery_succeeded(shopify_order_id: str) -> str:
    """Matches constitution v1.2.0 FR-010 dedup pattern; one per recovered order."""
    return f"{shopify_order_id}:recovery_succeeded"


def dedup_key_recovery_abandoned(shopify_order_id: str) -> str:
    """One fire per abandoned order, even if the abandon transition is retried."""
    return f"{shopify_order_id}:recovery_abandoned"
