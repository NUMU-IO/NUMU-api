"""Trust Network contribution feed (P1-7.5).

Mirrors NUMU's internal reputation writes to the standalone NUMU Trust Network's
``POST /v1/events`` so the cross-partner graph (and the Phase-2 model) is fed by NUMU's
real COD outcomes. NUMU is partner #1 of N — the endpoint and this shape are
partner-agnostic; other partners feed the same ``/v1/events`` via raw phone or an
edge-token so everyone joins one token space.

Discipline (mirrors ``trust_network_shadow``):
- **Env-gated + off by default** — set ``TRUST_NETWORK_FEED_ENABLED`` to turn it on.
- **Fail-open** — any error / timeout / non-2xx logs and returns ``False``; NUMU's own
  reputation write is never affected.
- **No raw PII leaves NUMU** — we send ``phone_hash`` as the ``buyer_token``. With the
  Trust Network's ``K_net`` set to NUMU's ``PLATFORM_SECRET_SALT`` (byte-identical
  tokenization, locked by the parity test), that hash IS the network token.

Config reuses the shadow's ``TRUST_NETWORK_URL`` / ``TRUST_NETWORK_API_KEY`` (read from
``os.environ`` — kept out of settings.py while experimental, same as the shadow).
"""

from __future__ import annotations

import os

import httpx

from src.core.logging import get_logger

logger = get_logger(__name__)


def feed_config() -> dict[str, object]:
    """Read the feed config from the environment."""
    raw_enabled = os.environ.get("TRUST_NETWORK_FEED_ENABLED", "").strip().lower()
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


async def post_outcome(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    phone_hash: str,
    event_type: str,
    dedup_key: str,
) -> bool:
    """POST one outcome using an EXISTING client, so callers can reuse a connection and
    drive their own concurrency (the backfill posts tens of thousands of rows). Returns
    ``True`` when recorded, else ``False``. Never raises — fail-open. ``dedup_key``
    doubles as the idempotency key (the Trust Network's ``contribution_log`` has a unique
    ``dedup_key``, so a retried/replayed outcome is a no-op)."""
    if not url or not phone_hash or not dedup_key:
        return False
    try:
        resp = await client.post(
            f"{url}/v1/events",
            json={
                "buyer_token": phone_hash,
                "event_type": event_type,
                "dedup_key": dedup_key,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Idempotency-Key": dedup_key,
            },
        )
    except Exception as exc:  # noqa: BLE001 — the feed never affects NUMU's own writes
        logger.warning(
            "trust_network_feed_error", error=str(exc), event_type=event_type
        )
        return False
    if resp.status_code // 100 != 2:
        logger.warning(
            "trust_network_feed_non_2xx",
            status=resp.status_code,
            event_type=event_type,
        )
        return False
    return True


async def send_network_outcome(
    *,
    phone_hash: str,
    event_type: str,
    dedup_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Ongoing feed: gate on ``TRUST_NETWORK_FEED_ENABLED`` + config, then POST one
    outcome with a short-lived client. Returns ``True`` when recorded, else ``False``
    (disabled / unconfigured / any failure). Never raises — fail-open."""
    cfg = feed_config()
    if not cfg["enabled"] or not cfg["url"] or not phone_hash or not dedup_key:
        return False
    async with httpx.AsyncClient(timeout=cfg["timeout"], transport=transport) as client:
        return await post_outcome(
            client,
            url=str(cfg["url"]),
            api_key=str(cfg["api_key"]),
            phone_hash=phone_hash,
            event_type=event_type,
            dedup_key=dedup_key,
        )
