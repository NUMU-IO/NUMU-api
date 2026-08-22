"""Data-subject-rights propagation to the standalone Trust Network.

NUMU dual-writes buyer outcomes to the standalone TN (``trust_network_feed``),
so a GDPR request handled here must reach the TN too — the privacy policy
promises that "the network signal forgets". The TN exposes
``POST /v1/data-subjects/erasure`` and ``POST /v1/data-subjects/export``
keyed by the network token, which is byte-identical to NUMU's
``phone_hash`` (K_net == PLATFORM_SECRET_SALT).

Config mirrors ``trust_network_feed``: plain env vars
(``TRUST_NETWORK_URL`` / ``TRUST_NETWORK_API_KEY`` /
``TRUST_NETWORK_TIMEOUT_SECONDS``), read at call time. Propagation is
active whenever URL + key are set — consent flags gate *writes*, but an
erasure duty exists for anything already contributed.

NOTE: the partner API key must carry the ``data_subjects:read`` +
``data_subjects:write`` scopes, or the TN answers 403 and the erasure
task alerts after exhausting retries.
"""

from __future__ import annotations

import os

import httpx

from src.core.logging import get_logger

logger = get_logger(__name__)


def privacy_config() -> dict[str, object]:
    """Read the TN connection config from the environment."""
    try:
        timeout = float(os.environ.get("TRUST_NETWORK_TIMEOUT_SECONDS", "5.0") or 5.0)
    except ValueError:
        timeout = 5.0
    url = os.environ.get("TRUST_NETWORK_URL", "").rstrip("/")
    api_key = os.environ.get("TRUST_NETWORK_API_KEY", "")
    return {
        "enabled": bool(url and api_key),
        "url": url,
        "api_key": api_key,
        "timeout": timeout,
    }


async def post_erasure(
    phone_hash: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[bool, str]:
    """Erase the subject from the standalone TN.

    Returns ``(done, detail)``. ``done`` is True when the TN confirmed
    the erasure OR the integration is not configured (nothing to erase
    from). False means the caller must retry — the Celery task owns the
    retry/alert policy.
    """
    cfg = privacy_config()
    if not cfg["enabled"]:
        return True, "not_configured"
    if not phone_hash:
        return True, "no_token"

    owns = client is None
    http = client or httpx.AsyncClient(timeout=float(cfg["timeout"]))  # type: ignore[arg-type]
    try:
        try:
            resp = await http.post(
                f"{cfg['url']}/v1/data-subjects/erasure",
                json={"token": phone_hash, "cluster_wide": False},
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
            )
        except Exception as exc:  # noqa: BLE001 — transport failure → retry
            logger.warning("trust_network_erasure_transport_error", error=str(exc))
            return False, f"transport_error: {exc}"
    finally:
        if owns:
            await http.aclose()

    if resp.status_code == 200:
        logger.info(
            "trust_network_erasure_done",
            token_prefix=phone_hash[:8],
            status=resp.status_code,
        )
        return True, "erased"
    if resp.status_code in (401, 403):
        # Key/scope misconfiguration — retrying gives ops a window to add
        # data_subjects:write to the partner key; the task alerts on
        # exhaustion either way.
        logger.warning(
            "trust_network_erasure_denied",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return False, f"denied_{resp.status_code}"
    logger.warning(
        "trust_network_erasure_failed",
        status=resp.status_code,
        body=resp.text[:200],
    )
    return False, f"http_{resp.status_code}"


async def fetch_export(
    phone_hash: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict | None:
    """Fetch the subject's TN export for a customers/data_request.

    Returns the TN export dict, or None when the integration is off,
    the token is empty, or the call failed (callers surface an explicit
    ``unavailable`` status rather than pretending completeness).
    """
    cfg = privacy_config()
    if not cfg["enabled"] or not phone_hash:
        return None

    owns = client is None
    http = client or httpx.AsyncClient(timeout=float(cfg["timeout"]))  # type: ignore[arg-type]
    try:
        try:
            resp = await http.post(
                f"{cfg['url']}/v1/data-subjects/export",
                json={"token": phone_hash, "cluster_wide": False},
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trust_network_export_transport_error", error=str(exc))
            return None
    finally:
        if owns:
            await http.aclose()

    if resp.status_code != 200:
        logger.warning(
            "trust_network_export_failed",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return None
    try:
        return resp.json()
    except ValueError:
        return None
