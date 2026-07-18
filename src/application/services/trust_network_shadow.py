"""Live equivalence gate: shadow-compare NUMU's embedded COD scorer against the
standalone NUMU Trust Network ``/v1/decisions``.

This is the final proof before any cutover — it turns "replayed history matches"
into "live production traffic matches". It runs in parallel to the real scorer and
**decides nothing**.

Discipline:
- OFF by default (env-gated) — set ``TRUST_NETWORK_SHADOW_ENABLED`` to turn it on.
- Best-effort — any error/timeout/non-200 logs and returns ``None``; the real
  decision is never affected (constitution fail-open).
- No raw PII leaves NUMU — the request is rebuilt from the captured
  ``decision_inputs`` determinants: a synthetic address of the recorded length, a
  canonical number for the recorded phone state, and the frozen network score.
  These reproduce every factor exactly (the same trick the service's golden replay
  uses), so a match is meaningful and a privacy boundary is preserved.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# phone_state -> a canonical number reproducing the same phone_validation branch.
_PHONE_BY_STATE: dict[str, str | None] = {
    "valid": "+201012345678",
    "invalid": "0",
    "absent": None,
}


def _synth_phone(phone_state: Any) -> str | None:
    return _PHONE_BY_STATE.get(phone_state) if isinstance(phone_state, str) else None


def _synth_address(address_length: Any) -> str | None:
    if not isinstance(address_length, int) or address_length <= 0:
        return None
    return "x" * address_length


def build_shadow_request(decision_inputs: dict[str, Any] | None) -> dict[str, Any]:
    """Map a captured ``decision_inputs`` snapshot to a ``/v1/decisions`` request that
    reproduces all nine factors — frozen network score, synthetic (non-PII) address /
    phone from their determinants. ``None`` fields are dropped."""
    di = decision_inputs or {}
    request: dict[str, Any] = {
        "total_cents": di.get("total_cents"),
        "payment_method": di.get("payment_method"),
        "avg_order_cents": di.get("avg_order_cents"),
        "customer_total_orders": di.get("customer_total_orders"),
        "customer_cancellation_rate": di.get("customer_cancellation_rate"),
        "created_at": di.get("created_at"),
        "product_tags": di.get("product_tags") or None,
        "network_score": di.get("network_score"),
        "network_label": di.get("network_label"),
        "address": _synth_address(di.get("address_length")),
        "phone": _synth_phone(di.get("phone_state")),
    }
    return {k: v for k, v in request.items() if v is not None}


def shadow_config() -> dict[str, Any]:
    """Read the shadow config from the environment (kept out of settings.py so this
    experimental gate has zero blast radius; move to settings on graduation)."""
    raw_enabled = os.environ.get("TRUST_NETWORK_SHADOW_ENABLED", "").strip().lower()
    try:
        timeout = float(os.environ.get("TRUST_NETWORK_TIMEOUT_SECONDS", "3.0") or 3.0)
    except ValueError:
        timeout = 3.0
    return {
        "enabled": raw_enabled in ("1", "true", "yes", "on"),
        "url": os.environ.get("TRUST_NETWORK_URL", "").rstrip("/"),
        "api_key": os.environ.get("TRUST_NETWORK_API_KEY", ""),
        "timeout": timeout,
    }


def resolve_authoritative_score(
    *,
    numu_risk_score: int,
    tn_comparison: dict[str, Any] | None,
    cutover_enabled: bool,
) -> tuple[int, str]:
    """Pick the authoritative COD risk score — the P1-7 cutover decision.

    Returns ``(score, source)``. ``source`` is ``"network"`` when the cutover is
    on AND the Trust Network returned a usable score; otherwise ``"numu"``.

    Fail-open by construction: when ``cutover_enabled`` is True but
    ``tn_comparison`` is ``None`` (network disabled / timeout / non-200) or the
    service score is missing/non-numeric, the score falls back to NUMU's
    embedded ``score_order`` result — so COD scoring never depends on network
    availability (constitution: never block the order flow on the network).
    """
    if cutover_enabled and tn_comparison:
        service = tn_comparison.get("service_risk_score")
        if isinstance(service, int | float) and not isinstance(service, bool):
            return int(service), "network"
    return numu_risk_score, "numu"


def network_factors_to_numu(service_factors: Any) -> list[dict[str, Any]]:
    """Map the Trust Network's ``FactorOut`` list to NUMU's persisted
    ``factors`` shape.

    Network sends ``{factor, score, weight, reason}`` (it was forked from NUMU's
    engine); NUMU persists ``{name, score, weight, detail}``. Under cutover the
    network's score becomes authoritative, so its factors must replace NUMU's on
    the assessment row or the stored explanation would describe a different
    score. Returns ``[]`` on missing/malformed input (the caller substitutes a
    provenance marker so factors are never silently mismatched with the score).
    """
    mapped: list[dict[str, Any]] = []
    for f in service_factors or []:
        if not isinstance(f, dict):
            continue
        mapped.append({
            "name": f.get("factor"),
            "score": f.get("score"),
            "weight": f.get("weight"),
            "detail": f.get("reason"),
        })
    return mapped


async def compare_with_trust_network(
    *,
    decision_inputs: dict[str, Any] | None,
    numu_risk_score: int,
    order_ref: str = "",
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """Call the service in parallel and log the agreement. Returns the comparison
    (or ``None`` when disabled / unconfigured / on any failure)."""
    cfg = shadow_config()
    if not cfg["enabled"] or not cfg["url"] or not decision_inputs:
        return None
    request = build_shadow_request(decision_inputs)
    try:
        async with httpx.AsyncClient(
            timeout=cfg["timeout"], transport=transport
        ) as client:
            resp = await client.post(
                f"{cfg['url']}/v1/decisions",
                json=request,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Idempotency-Key": f"shadow-{order_ref or numu_risk_score}",
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "trust_network_shadow non_200 status=%s order=%s",
                resp.status_code,
                order_ref,
            )
            return None
        body = resp.json()
        service_score = body.get("risk_score")
        if service_score is None:
            return None
        drift = abs(int(service_score) - int(numu_risk_score))
        match = drift == 0
        (logger.info if match else logger.warning)(
            "trust_network_shadow %s order=%s numu=%s service=%s drift=%s",
            "MATCH" if match else "DRIFT",
            order_ref,
            numu_risk_score,
            service_score,
            drift,
        )
        return {
            "numu_risk_score": numu_risk_score,
            "service_risk_score": service_score,
            # The network's own factor breakdown — persisted under cutover so the
            # stored explanation matches the network's authoritative score
            # (raw FactorOut dicts: {factor, score, weight, reason}).
            "service_factors": body.get("factors") or [],
            "drift": drift,
            "match": match,
        }
    except Exception as exc:  # noqa: BLE001 — shadow never affects scoring
        logger.warning("trust_network_shadow error order=%s err=%s", order_ref, exc)
        return None
