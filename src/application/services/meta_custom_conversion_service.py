"""Auto-create a Meta Custom Conversion at marketing-campaign send time.

Spec 005 US6 v2 foundation. Without a Custom Conversion, Meta's
Ads Manager and ``/insights`` API don't break Purchase events down by
``custom_data.numu_utm_campaign`` — even though NUMU forwards that
field with every CAPI event (see PR #337). The merchant would have to
manually create the Custom Conversion in Events Manager for every
campaign, which is unrealistic.

This service is invoked from the dispatcher right before the recipient
loop runs. It POSTs ``/{ad_account_id}/customconversions`` with a rule
that matches ``custom_data.numu_utm_campaign EQUALS {short_code}`` and
fires on Meta's Purchase event. The returned conversion id is cached
on the campaign so subsequent ``/insights`` queries can reference it.

Best-effort: returns ``None`` on any failure. The dispatcher never
blocks on this — message delivery is the priority; attribution is a
nice-to-have.

Reuses the same ``access_token`` + ``ad_account_id`` + ``pixel_id``
already on file from the OAuth flow (Wave 3 Phase 17).
"""

from __future__ import annotations

import json as _json
from typing import Any

import httpx

from src.config import settings as _app_settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


async def create_meta_custom_conversion_for_campaign(
    *,
    ad_account_id: str,
    pixel_id: str,
    access_token: str,
    campaign_short_code: str,
    campaign_name: str,
) -> str | None:
    """Create a Meta Custom Conversion keyed on this campaign's UTM.

    Returns the new ``custom_conversion_id`` on success, ``None`` on
    Marketing-API failure. Caller logs + persists when non-None.

    The Custom Conversion fires only when a Purchase event arrives at
    the merchant's pixel with ``custom_data.numu_utm_campaign``
    matching ``campaign_short_code``. NUMU stamps that field via the
    Purchase CAPI dispatcher (PR #337) on every webhook-triggered
    Purchase.

    ``custom_event_type=PURCHASE`` ties the conversion to Meta's
    standard Purchase event so the merchant's existing ad campaigns
    optimizing for Purchase pick it up as an additional dimension
    rather than a new event class. ``rule`` is a JSON-encoded matcher
    string per Meta's API (the ``and``/``or`` envelope is required even
    for a single clause; we wrap once).
    """
    api_version = getattr(_app_settings, "meta_graph_api_version", "v21.0")
    clean_act_id = ad_account_id.removeprefix("act_")
    url = (
        f"https://graph.facebook.com/{api_version}/act_{clean_act_id}/customconversions"
    )

    rule = {
        "and": [
            {
                "event.custom_data.numu_utm_campaign": {
                    "eq": campaign_short_code,
                },
            },
        ],
    }

    # Name must be <= 50 chars per Meta's API. Truncate the campaign
    # name aggressively to leave room for the "NUMU · " prefix that
    # makes it findable in the merchant's Custom Conversion list.
    name = f"NUMU · {campaign_name}"[:50]

    payload = {
        "name": name,
        "description": (
            f"Auto-created by NUMU. Tracks Purchase events with "
            f"utm_campaign={campaign_short_code}."
        )[:255],
        "pixel_id": pixel_id,
        "event_source_type": "pixel",
        "custom_event_type": "PURCHASE",
        "rule": _json.dumps(rule),
        "access_token": access_token,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data=payload)
        if resp.status_code >= 400:
            logger.warning(
                "meta_custom_conversion_create_failed",
                extra={
                    "ad_account_id": clean_act_id,
                    "pixel_id": pixel_id,
                    "short_code": campaign_short_code,
                    "status": resp.status_code,
                    "body": resp.text[:300],
                },
            )
            return None
        body: dict[str, Any] = resp.json() if resp.content else {}
        return str(body.get("id")) if body.get("id") else None
    except Exception as exc:  # noqa: BLE001 — fail-open for caller
        logger.warning(
            "meta_custom_conversion_create_exception",
            extra={
                "ad_account_id": clean_act_id,
                "short_code": campaign_short_code,
                "error": str(exc),
            },
        )
        return None
