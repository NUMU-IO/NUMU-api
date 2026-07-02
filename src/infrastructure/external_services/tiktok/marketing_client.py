"""TikTok Marketing API client — reporting (spend / impressions / conversions).

Thin async wrapper around ``report/integrated/get``. Uses the merchant's stored
access token (the OAuth token persisted as the TIKTOK_CAPI credential) + the
``advertiser_id`` from ``store.settings.tracking.tiktok``. Only works when the
token carries advertiser scope — i.e. it came from the P4 OAuth flow, not a
hand-pasted Events API token. Callers gate on ``advertiser_id`` presence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from src.config.logging_config import get_logger

logger = get_logger(__name__)

_BASE = "https://business-api.tiktok.com"
_API_VERSION = "v1.3"

# The metrics we surface in the hub reporting card. TikTok returns them as
# strings; the caller coerces to numbers.
_REPORT_METRICS: tuple[str, ...] = (
    "spend",
    "impressions",
    "clicks",
    "conversion",
    "cost_per_conversion",
    "ctr",
)


class TikTokMarketingError(Exception):
    """Raised when TikTok returns an error from a Marketing API endpoint."""


@dataclass(frozen=True)
class TikTokReport:
    """Aggregated advertiser-level report for a date range."""

    spend: float
    impressions: int
    clicks: int
    conversions: float
    cost_per_conversion: float
    ctr: float
    currency: str | None


class TikTokMarketingClient:
    """Stateless async client for the TikTok Marketing reporting API."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def get_advertiser_report(
        self,
        *,
        access_token: str,
        advertiser_id: str,
        start_date: str,
        end_date: str,
    ) -> TikTokReport:
        """Fetch an aggregated advertiser-level basic report.

        ``start_date`` / ``end_date`` are ``YYYY-MM-DD``. Returns zeroed
        metrics when TikTok reports no rows for the window (a fresh or paused
        advertiser) rather than raising.
        """
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.get(
                f"{_BASE}/open_api/{_API_VERSION}/report/integrated/get/",
                params={
                    "advertiser_id": advertiser_id,
                    "report_type": "BASIC",
                    "data_level": "AUCTION_ADVERTISER",
                    "dimensions": json.dumps(["advertiser_id"]),
                    "metrics": json.dumps(list(_REPORT_METRICS)),
                    "start_date": start_date,
                    "end_date": end_date,
                    "page_size": 1,
                },
                headers={"Access-Token": access_token},
            )
            data = _parse_envelope(resp)
            rows = data.get("list") or []
            metrics = (rows[0].get("metrics") if rows else {}) or {}
            return TikTokReport(
                spend=_num(metrics.get("spend")),
                impressions=int(_num(metrics.get("impressions"))),
                clicks=int(_num(metrics.get("clicks"))),
                conversions=_num(metrics.get("conversion")),
                cost_per_conversion=_num(metrics.get("cost_per_conversion")),
                ctr=_num(metrics.get("ctr")),
                currency=None,
            )
        finally:
            if self._client is None:
                await client.aclose()


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_envelope(resp: httpx.Response) -> dict[str, Any]:
    """Parse TikTok's ``{code, message, data}`` envelope (non-zero code = error)."""
    if resp.status_code >= 400:
        raise TikTokMarketingError(
            f"TikTok Marketing HTTP {resp.status_code}: {resp.text[:500]}"
        )
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise TikTokMarketingError(
            f"TikTok Marketing non-JSON response: {resp.text[:300]}"
        ) from exc
    if body.get("code") not in (0, None):
        raise TikTokMarketingError(
            f"TikTok Marketing code {body.get('code')}: {body.get('message')}"
        )
    return body.get("data") or {}
