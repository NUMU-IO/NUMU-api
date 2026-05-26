"""Promote a sent marketing campaign on Meta as a draft (PAUSED) ad.

Spec 005 US7. Lets a merchant fork a completed email/SMS campaign into
a Meta ad in their Ad Account with one click — same creative (hero
image + headline + CTA), targeted at the synced Custom Audience for
the campaign's segment (when one exists).

**Always PAUSED.** We don't auto-launch paid ad spend from NUMU; the
merchant deliberately activates the ad in Meta Ads Manager. This both
removes a liability surface (NUMU never instructs Meta to charge the
merchant) and gives the merchant a chance to set budget / bid / schedule
in Meta's UI before going live.

Two-call flow:
  1. POST /act_{ad_account_id}/adcreatives → returns creative_id
  2. POST /act_{ad_account_id}/ads → returns ad_id (status=PAUSED)

Both calls are best-effort + return None on failure so the caller can
surface a 502 instead of crashing. Meta's error envelope is logged
verbatim for debugging.

Requires the merchant's OAuth token to have ``ads_management`` scope.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from src.config import settings as _app_settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


# Meta's CTA button labels for link ads. The merchant typically wants
# "SHOP_NOW" for an e-commerce promotion, but we expose the option in
# case a campaign promoted a content page rather than a product.
SHOP_NOW_CTA = "SHOP_NOW"


def _ads_manager_url(act_id: str, ad_id: str) -> str:
    """Deep-link to the merchant's draft ad in Meta Ads Manager.

    Returning this in the response lets the hub render a "View on Meta"
    button so the merchant can immediately go set budget / schedule /
    bid + flip the ad from PAUSED to ACTIVE without hunting for it.
    """
    qs = urlencode({
        "act": act_id.removeprefix("act_"),
        "selected_ad_ids": ad_id,
    })
    return f"https://adsmanager.facebook.com/adsmanager/manage/ads?{qs}"


def _summarize_meta_error(body_text: str) -> str:
    """Pull Meta's user-facing message out of an error response.

    Meta error envelope looks like::

        {"error": {"message": "...", "code": 100, "error_subcode": 33,
                   "fbtrace_id": "..."}}

    We surface ``error.message`` because that's what merchants need to
    act on. ``error_subcode = 33`` in particular is the "missing
    permissions" tell — most commonly the token lacks
    ``ads_management``. We append actionable guidance for that case
    so the route can surface a useful toast instead of a bare 502.
    """
    import json as _json

    try:
        envelope = _json.loads(body_text)
    except Exception:
        return body_text[:240] or "Meta returned an unstructured error."
    err = envelope.get("error") or {}
    message = err.get("message") or "Meta rejected the request."
    subcode = err.get("error_subcode")
    if subcode == 33:
        return (
            f"{message} (Meta error subcode 33: the saved access token "
            "likely lacks the 'ads_management' permission. Generate a "
            "new System User token with ads_management scope on the "
            "Ad Account and paste it in Settings → Tracking & Pixels.)"
        )
    return message


async def promote_campaign_on_meta(
    *,
    ad_account_id: str,
    page_id: str,
    access_token: str,
    campaign_name: str,
    headline: str,
    body_text: str,
    image_url: str,
    link_url: str,
    custom_audience_id: str | None = None,
    cta_type: str = SHOP_NOW_CTA,
) -> dict[str, Any]:
    """Create a PAUSED draft ad mirroring the campaign's creative.

    Returns one of two shapes:

      * ``{"ad_id", "creative_id", "ads_manager_url"}`` on success
      * ``{"error": "<Meta's actual message>"}`` on failure

    Failure shape replaces the previous ``None`` return so the route
    can surface Meta's actual reason in the HTTP response — opaque
    502s left the merchant with nothing actionable when their token
    lacked ads_management scope (subcode 33), the ad account had no
    default ad set, or the image URL wasn't publicly fetchable.

    ``custom_audience_id`` is optional — when set, ad targeting
    narrows to that audience (typically the synced Lookalike or
    Custom Audience built from the campaign's segment). When None,
    the ad ships with empty targeting; the merchant fills it in
    Ads Manager before publishing.
    """
    api_version = getattr(_app_settings, "meta_graph_api_version", "v21.0")
    clean_act_id = ad_account_id.removeprefix("act_")
    creatives_url = (
        f"https://graph.facebook.com/{api_version}/act_{clean_act_id}/adcreatives"
    )
    ads_url = f"https://graph.facebook.com/{api_version}/act_{clean_act_id}/ads"

    # Step 1: create the creative.
    object_story_spec = {
        "page_id": page_id,
        "link_data": {
            "link": link_url,
            "message": body_text[:2000],  # Meta caps message ~2k chars
            "name": headline[:255],
            "call_to_action": {"type": cta_type, "value": {"link": link_url}},
        },
    }
    # Attach the image if present. Meta accepts URLs directly for the
    # ``image_url`` field — they fetch + host the asset themselves.
    if image_url:
        object_story_spec["link_data"]["picture"] = image_url

    creative_payload = {
        "name": f"NUMU · {campaign_name[:80]}",
        "object_story_spec": __import__("json").dumps(object_story_spec),
        "access_token": access_token,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            creative_resp = await client.post(creatives_url, data=creative_payload)
        if creative_resp.status_code >= 400:
            logger.warning(
                "meta_promote_creative_failed",
                extra={
                    "ad_account_id": clean_act_id,
                    "status": creative_resp.status_code,
                    "body": creative_resp.text[:400],
                },
            )
            return {"error": _summarize_meta_error(creative_resp.text)}
        creative_body = creative_resp.json() if creative_resp.content else {}
        creative_id = str(creative_body.get("id") or "")
        if not creative_id:
            return {
                "error": "Meta accepted the creative request but did not return an id."
            }
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "meta_promote_creative_exception",
            extra={"ad_account_id": clean_act_id, "error": str(exc)},
        )
        return {
            "error": f"Network error reaching Meta during ad creative creation: {exc}"
        }

    # Step 2: create the ad (PAUSED).
    targeting: dict[str, Any] = {
        # Even with no Custom Audience, Meta requires at least a country
        # spec on targeting. Default to Egypt — the merchant can broaden
        # in Ads Manager before publishing.
        "geo_locations": {"countries": ["EG"]},
    }
    if custom_audience_id:
        targeting["custom_audiences"] = [{"id": custom_audience_id}]

    ad_payload = {
        "name": f"NUMU · {campaign_name[:80]}",
        "creative": __import__("json").dumps({"creative_id": creative_id}),
        "targeting": __import__("json").dumps(targeting),
        "status": "PAUSED",  # ALWAYS PAUSED — merchant flips to ACTIVE in Meta UI
        "access_token": access_token,
        # ``adset_id`` is technically required on this endpoint, but Meta
        # will create a default ad set when omitted IF the ad_account has
        # a default campaign. For accounts without one, the merchant gets
        # a clear 4xx telling them to set up their first ad set first —
        # better than NUMU pretending to create infrastructure for them.
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            ad_resp = await client.post(ads_url, data=ad_payload)
        if ad_resp.status_code >= 400:
            logger.warning(
                "meta_promote_ad_failed",
                extra={
                    "ad_account_id": clean_act_id,
                    "creative_id": creative_id,
                    "status": ad_resp.status_code,
                    "body": ad_resp.text[:400],
                },
            )
            return {"error": _summarize_meta_error(ad_resp.text)}
        ad_body = ad_resp.json() if ad_resp.content else {}
        ad_id = str(ad_body.get("id") or "")
        if not ad_id:
            return {"error": "Meta accepted the ad request but did not return an id."}
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "meta_promote_ad_exception",
            extra={"ad_account_id": clean_act_id, "error": str(exc)},
        )
        return {"error": f"Network error reaching Meta during ad creation: {exc}"}

    return {
        "ad_id": ad_id,
        "creative_id": creative_id,
        "ads_manager_url": _ads_manager_url(clean_act_id, ad_id),
    }
