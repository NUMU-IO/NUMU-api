"""COD trust check service.

Decides whether a Cash-on-Delivery order should be allowed based on the
customer's cross-merchant reputation in the network_reputation table.

Design principles:
  * **Fail-open** — fraud filtering, not authentication. DB outages, missing
    settings, or unhashable phones must NEVER block a legitimate order.
  * **Confidence-aware** — never block on insufficient data (default
    `min_confidence="medium"`).
  * **Opt-in by default** — first deploy must not break existing stores.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.application.services.network_reputation_service import (
    extract_phone_hash_from_string,
    lookup_network_reputation,
)

if TYPE_CHECKING:
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
    )

logger = logging.getLogger(__name__)


# Defaults — opt-in. Merchants must explicitly enable from PaymentSetup.
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "threshold": 70,
    "min_confidence": "medium",  # never block on "low" confidence
    "action": "block",  # "block" | "warn"
}

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_VALID_CONFIDENCE = {"low", "medium", "high"}
_VALID_ACTIONS = {"block", "warn"}
_BASELINE_SCORE = 55  # matches network_reputation_service baseline


@dataclass(frozen=True)
class CodTrustDecision:
    """Result of a trust check."""

    allowed: bool
    reason: str | None
    score: int | None = None
    confidence: str | None = None
    label: str | None = None


def get_cod_trust_settings(store_settings: dict | None) -> dict[str, Any]:
    """Merge ``store.settings.cod_trust`` over DEFAULTS, validating types.

    Returns a fully-populated dict — never raises on bad input.
    """
    raw = (store_settings or {}).get("cod_trust") or {}
    if not isinstance(raw, dict):
        return dict(DEFAULTS)

    result = dict(DEFAULTS)

    # enabled — coerce to bool
    if "enabled" in raw:
        result["enabled"] = bool(raw["enabled"])

    # threshold — int, clamp to [0, 100]
    if "threshold" in raw:
        try:
            result["threshold"] = max(0, min(100, int(raw["threshold"])))
        except (TypeError, ValueError):
            pass  # keep default

    # min_confidence — must be one of valid values
    if raw.get("min_confidence") in _VALID_CONFIDENCE:
        result["min_confidence"] = raw["min_confidence"]

    # action — must be one of valid values
    if raw.get("action") in _VALID_ACTIONS:
        result["action"] = raw["action"]

    return result


async def check_customer_trust(
    *,
    phone: str | None,
    store_settings: dict | None,
    network_repo: NetworkReputationRepository,
) -> CodTrustDecision:
    """Evaluate whether a COD order from this customer should be allowed.

    Decision order (FAIL-OPEN on every error path):
        1. cod_trust.enabled is False    → allow ("disabled")
        2. phone None / unhashable        → allow ("no_phone")
        3. lookup raises                  → allow ("lookup_error", log warning)
        4. score is baseline (no record)  → allow ("new_customer")
        5. confidence < min_confidence    → allow ("low_confidence")
        6. score >= threshold + block     → DENY ("blocked_high_risk")
        7. score >= threshold + warn      → allow ("warned_high_risk", log)
        8. otherwise                       → allow ("below_threshold")
    """
    settings = get_cod_trust_settings(store_settings)

    # 1. Feature disabled
    if not settings["enabled"]:
        return CodTrustDecision(allowed=True, reason="disabled")

    # 2. Phone missing or invalid
    phone_hash = extract_phone_hash_from_string(phone)
    if not phone_hash:
        return CodTrustDecision(allowed=True, reason="no_phone")

    # 3. Lookup network reputation (never raises — fail-open)
    try:
        score, confidence, label = await lookup_network_reputation(
            phone_hash, network_repo
        )
    except Exception as exc:
        logger.warning("cod_trust_lookup_error: %s", exc)
        return CodTrustDecision(allowed=True, reason="lookup_error")

    # 4. New customer (baseline score, no record in network)
    if score == _BASELINE_SCORE and label == "new_to_network":
        return CodTrustDecision(
            allowed=True,
            reason="new_customer",
            score=score,
            confidence=confidence,
            label=label,
        )

    # 5. Confidence too low to act on
    min_conf_rank = _CONFIDENCE_RANK[settings["min_confidence"]]
    actual_conf_rank = _CONFIDENCE_RANK.get(confidence, 0)
    if actual_conf_rank < min_conf_rank:
        return CodTrustDecision(
            allowed=True,
            reason="low_confidence",
            score=score,
            confidence=confidence,
            label=label,
        )

    # 6 & 7. High-risk score → block or warn
    if score >= settings["threshold"]:
        if settings["action"] == "block":
            return CodTrustDecision(
                allowed=False,
                reason="blocked_high_risk",
                score=score,
                confidence=confidence,
                label=label,
            )
        # warn mode — allow but log
        logger.warning(
            "cod_trust_warned_high_risk score=%s confidence=%s label=%s",
            score,
            confidence,
            label,
        )
        return CodTrustDecision(
            allowed=True,
            reason="warned_high_risk",
            score=score,
            confidence=confidence,
            label=label,
        )

    # 8. Below threshold — all clear
    return CodTrustDecision(
        allowed=True,
        reason="below_threshold",
        score=score,
        confidence=confidence,
        label=label,
    )
