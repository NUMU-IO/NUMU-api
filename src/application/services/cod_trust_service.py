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
from dataclasses import dataclass, field
from math import atan2, cos, radians, sin, sqrt
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

# Location-signal weights added to the network reputation score. Individually
# none of these BLOCK on their own — they contribute to the same score the
# merchant's existing cod_trust.threshold evaluates. Numbers tuned to be
# meaningful but not punitive (a single signal won't cross a typical 70
# threshold starting from the 55 baseline).
_LOCATION_WEIGHT_NO_LOCATION = 15
_LOCATION_WEIGHT_TELEPORT = 20
_LOCATION_WEIGHT_LOW_ACCURACY = 10

# Distance above which we call it a "teleport" vs the customer's previous
# delivery. 50km covers normal intra-city movement (Cairo → 6 October) while
# flagging clearly-different cities (Cairo → Alexandria is ~220km).
_TELEPORT_KM_THRESHOLD = 50.0

# Accuracy floor for a GPS reading to count as reliable. Readings worse
# than this while claiming source="gps" are a mild risk signal (indoors,
# faked, or VPN).
_GPS_LOW_ACCURACY_M = 1000.0


@dataclass(frozen=True)
class LocationSignals:
    """Location context passed into the trust check.

    When a field is unknown or the customer didn't pin a location, leave
    it as the default None — the trust service interprets "no location"
    itself as a mild risk signal.
    """

    latitude: float | None = None
    longitude: float | None = None
    accuracy: float | None = None  # meters
    source: str | None = None  # "gps" | "manual_pin" | None
    # Coordinates of this customer's most-recent previous order, for
    # teleport detection. None means "first order" or no prior location
    # data — we can't evaluate teleport and skip that signal.
    previous_coords: tuple[float, float] | None = None


@dataclass(frozen=True)
class CodTrustDecision:
    """Result of a trust check."""

    allowed: bool
    reason: str | None
    score: int | None = None
    confidence: str | None = None
    label: str | None = None
    # Contributing signals: each factor is ``{"code": str, "weight": int,
    # "detail": str}``. Caller can persist to RiskAssessmentModel.factors.
    factors: list[dict[str, Any]] = field(default_factory=list)


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


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two WGS84 points in kilometers."""
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    )
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _evaluate_location_signals(
    location: LocationSignals,
) -> tuple[int, list[dict[str, Any]]]:
    """Score and describe the location-based risk signals.

    Returns ``(score_adjustment, factors)``. Factors are always
    returned — including informational ones with weight 0 — so the
    caller can log a complete picture.
    """
    adjustment = 0
    factors: list[dict[str, Any]] = []

    has_coords = location.latitude is not None and location.longitude is not None

    # Signal 1: customer did not confirm any location at all. Real buyers
    # overwhelmingly pin the map; refusers skew fraudulent.
    if not has_coords or location.source is None:
        adjustment += _LOCATION_WEIGHT_NO_LOCATION
        factors.append({
            "code": "no_location",
            "weight": _LOCATION_WEIGHT_NO_LOCATION,
            "detail": "Customer did not pin a delivery location.",
        })
        return adjustment, factors  # other signals need coords; skip them

    # Signal 2: teleport — the delivery coordinate jumped >50km from the
    # customer's last known delivery point.
    if location.previous_coords is not None:
        prev_lat, prev_lng = location.previous_coords
        distance_km = _haversine_km(
            location.latitude,  # type: ignore[arg-type]
            location.longitude,  # type: ignore[arg-type]
            prev_lat,
            prev_lng,
        )
        if distance_km > _TELEPORT_KM_THRESHOLD:
            adjustment += _LOCATION_WEIGHT_TELEPORT
            factors.append({
                "code": "location_teleport",
                "weight": _LOCATION_WEIGHT_TELEPORT,
                "detail": f"Delivery moved {distance_km:.0f}km from last order.",
            })

    # Signal 3: GPS claimed but accuracy is worse than a city block —
    # likely indoors-only fix, VPN, or spoofed.
    if (
        location.source == "gps"
        and location.accuracy is not None
        and location.accuracy > _GPS_LOW_ACCURACY_M
    ):
        adjustment += _LOCATION_WEIGHT_LOW_ACCURACY
        factors.append({
            "code": "low_accuracy_gps",
            "weight": _LOCATION_WEIGHT_LOW_ACCURACY,
            "detail": f"GPS reading ~{location.accuracy:.0f}m — low confidence.",
        })

    return adjustment, factors


async def check_customer_trust(
    *,
    phone: str | None,
    store_settings: dict | None,
    network_repo: NetworkReputationRepository,
    location: LocationSignals | None = None,
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

    # Location-based signals. Evaluated regardless of the network score so
    # the factors list stays informative even for new/below-threshold
    # customers. Never block on their own — they contribute to the same
    # score the merchant's threshold evaluates.
    location_adjustment = 0
    location_factors: list[dict[str, Any]] = []
    if location is not None:
        location_adjustment, location_factors = _evaluate_location_signals(location)

    adjusted_score = min(100, max(0, score + location_adjustment))

    # 4. New customer (baseline score, no record in network) — still apply
    # location signals; a brand-new account refusing to pin a location
    # should be flagged the same as any other refuser.
    if score == _BASELINE_SCORE and label == "new_to_network":
        return CodTrustDecision(
            allowed=True,
            reason="new_customer",
            score=adjusted_score,
            confidence=confidence,
            label=label,
            factors=location_factors,
        )

    # 5. Confidence too low to act on
    min_conf_rank = _CONFIDENCE_RANK[settings["min_confidence"]]
    actual_conf_rank = _CONFIDENCE_RANK.get(confidence, 0)
    if actual_conf_rank < min_conf_rank:
        return CodTrustDecision(
            allowed=True,
            reason="low_confidence",
            score=adjusted_score,
            confidence=confidence,
            label=label,
            factors=location_factors,
        )

    # 6 & 7. High-risk score (after location adjustment) → block or warn
    if adjusted_score >= settings["threshold"]:
        if settings["action"] == "block":
            return CodTrustDecision(
                allowed=False,
                reason="blocked_high_risk",
                score=adjusted_score,
                confidence=confidence,
                label=label,
                factors=location_factors,
            )
        # warn mode — allow but log
        logger.warning(
            "cod_trust_warned_high_risk score=%s confidence=%s label=%s factors=%s",
            adjusted_score,
            confidence,
            label,
            [f["code"] for f in location_factors],
        )
        return CodTrustDecision(
            allowed=True,
            reason="warned_high_risk",
            score=adjusted_score,
            confidence=confidence,
            label=label,
            factors=location_factors,
        )

    # 8. Below threshold — all clear
    return CodTrustDecision(
        allowed=True,
        reason="below_threshold",
        score=adjusted_score,
        confidence=confidence,
        label=label,
        factors=location_factors,
    )
