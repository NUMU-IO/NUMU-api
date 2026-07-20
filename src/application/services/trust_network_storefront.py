"""Storefront COD gate → Trust Network decisions (the full-partner read path).

The native storefront's COD trust gate historically read NUMU's LOCAL
``network_reputation`` table. This service lets it consult the standalone
Trust Network's ``/v1/decisions`` instead — the TN's OWN graph (cross-partner
data), FSM, and ML shadow all engage, and every storefront checkout accrues a
TN shadow row whose outcome label later feeds the §5.6 promotion gate.

Discipline (matches the shadow/feed modules):
- OFF by default — ``TRUST_NETWORK_STOREFRONT_ENABLED`` gates it; the URL/key
  are the same ``TRUST_NETWORK_URL``/``TRUST_NETWORK_API_KEY`` already deployed.
- Fail-open + latency-capped — the checkout is a synchronous user path, so the
  call runs with NO retries and a tight timeout (default 2s); any failure
  returns ``None`` and the caller falls back to the local lookup.
- No raw PII leaves — the buyer travels as ``buyer_token`` (NUMU's phone_hash;
  tokenization is K_net-aligned so it IS the network token).

Built on the vendored async SDK client (``trust_network_async_sdk``) — the same
client external partners get, dogfooded.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.infrastructure.external_services.trust_network_async_sdk import (
    AsyncTrustNetworkClient,
)

logger = logging.getLogger(__name__)


def storefront_tn_config() -> dict[str, Any]:
    """Read the storefront-gate config from the environment (feed/shadow pattern)."""
    raw_enabled = os.environ.get("TRUST_NETWORK_STOREFRONT_ENABLED", "").strip().lower()
    try:
        timeout = float(
            os.environ.get("TRUST_NETWORK_STOREFRONT_TIMEOUT_SECONDS", "2.0") or 2.0
        )
    except ValueError:
        timeout = 2.0
    return {
        "enabled": raw_enabled in ("1", "true", "yes", "on"),
        "url": os.environ.get("TRUST_NETWORK_URL", "").rstrip("/"),
        "api_key": os.environ.get("TRUST_NETWORK_API_KEY", ""),
        "timeout": timeout,
    }


async def fetch_network_intelligence(
    *,
    phone_hash: str,
    total_cents: int | None = None,
    idempotency_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, str, str] | None:
    """Consult the Trust Network for this buyer; ``(score, confidence, label)``.

    Two modes, honest about what the caller knows:
    - ``total_cents`` known → a full ``/v1/decisions`` call (``total_cents`` is
      REQUIRED by the TN's DecisionRequest; the FSM + ML shadow engage and a
      shadow row accrues toward the live promotion gate).
    - ``total_cents`` unknown (the storefront gate runs before totals are
      computed) → ``/v1/reputation/buyer/{token}`` — the reputation read-model,
      exact semantic parity with the local lookup, no fabricated order inputs.

    Returns ``None`` when disabled / unconfigured / on ANY failure — the caller
    falls back to the local reputation lookup (fail-open, never blocks checkout
    on network availability).
    """
    cfg = storefront_tn_config()
    if not cfg["enabled"] or not cfg["url"] or not cfg["api_key"]:
        return None
    try:
        async with AsyncTrustNetworkClient(
            cfg["api_key"],
            base_url=cfg["url"],
            timeout=cfg["timeout"],
            max_retries=0,  # checkout latency budget — fail open, don't retry
            transport=transport,
        ) as tn:
            if total_cents is not None:
                body = await tn.decide(
                    {
                        "total_cents": int(total_cents),
                        "payment_method": "cod",
                        "buyer_token": phone_hash,
                    },
                    idempotency_key=idempotency_key,
                )
                score = body.get("risk_score")
                confidence = body.get("confidence")
                label = body.get("network_label")
                mode = "decision"
            else:
                body = await tn.get_buyer_reputation(phone_hash)
                score = body.get("network_risk_score")
                confidence = body.get("confidence")
                label = body.get("label")
                mode = "reputation"
        if not isinstance(score, int) or not confidence or not label:
            return None
        logger.info(
            "trust_network_storefront %s score=%s confidence=%s label=%s",
            mode,
            score,
            confidence,
            label,
        )
        return score, str(confidence), str(label)
    except Exception as exc:  # noqa: BLE001 — fail-open: checkout never depends on TN
        logger.warning("trust_network_storefront error err=%s", exc)
        return None
