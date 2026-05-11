"""Server-side analytics fanout (Phase 4.3).

The storefront's `useAnalytics()` hook (already shipped in Phase 2)
fires `numu:analytics:event` window events AND POSTs to
`/api/storefront/track`. This module is what receives the POST and
fans the event out to whichever pixel providers the merchant has
configured: GA4 Measurement Protocol, Meta Conversions API, TikTok
Events API.

Why server-side fanout instead of client-side pixels:
  - iOS Safari + ad-blockers strip third-party pixel scripts; the
    server-side path runs from our IP and survives.
  - Conversion APIs (Meta CAPI, GA4 MP) deliberately accept events
    via HTTP POST as a complement to client tracking — running
    BOTH (server enriched with order data + client for view events)
    is the documented pattern.
  - Merchants set tokens once in the hub instead of pasting pixel
    script tags into every theme.

Configuration shape (`store.settings.analytics`):

    {
      "ga4": {
        "measurement_id": "G-XXXXXX",
        "api_secret":     "<secret from GA admin>"
      },
      "meta_capi": {
        "pixel_id":     "1234567890",
        "access_token": "<long-lived token>"
      },
      "tiktok": {
        "pixel_id":     "ABC123",
        "access_token": "<token>"
      }
    }

v1 ships the dispatcher + config plumbing. The actual provider
integrations (GA4 / Meta / TikTok HTTP POSTs) are scaffolded as
typed functions; provider credentials get hooked through a follow-up
once the merchant hub adds the settings UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Standard event names that GA4 / Meta CAPI / TikTok all recognize.
# Merchants can fire custom events with arbitrary names; the providers
# just record them as custom_event entries. We intentionally don't
# enforce a whitelist — surprise-restricting valid GA4 events at this
# layer would break themes that were already working.
STANDARD_EVENTS = {
    "page_view",
    "view_item",
    "view_collection",
    "search",
    "add_to_cart",
    "remove_from_cart",
    "view_cart",
    "begin_checkout",
    "add_payment_info",
    "add_shipping_info",
    "purchase",
    "refund",
    "sign_up",
    "login",
    "add_to_wishlist",
    "share",
}


@dataclass(frozen=True)
class AnalyticsEvent:
    """Normalized event the dispatcher hands to each provider."""

    event_name: str
    payload: dict[str, Any]
    # Stable client identifier — falls back to a per-session uuid when
    # neither the customer nor a tracking cookie is set. The providers
    # all want some correlation key for sessionization.
    client_id: str
    # Authenticated customer when present — providers attach LTV-style
    # signals if they recognize the user.
    customer_email: str | None = None
    customer_phone: str | None = None
    # Source IP / UA — required by Meta CAPI for the
    # "advanced match" + de-duplication with client-side pixel fires.
    source_ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    """Per-provider delivery outcome.

    Caller logs the aggregate; we don't surface per-event failures to
    the storefront because pixel failures must never block UX.
    """

    provider: str
    delivered: bool
    error: str | None = None


class AnalyticsDispatcher:
    """Fans an event out to all merchant-configured providers.

    Stateless — instantiate per request. Each `dispatch()` call walks
    the configured providers and sends in parallel; failures are
    swallowed + logged so a flaky pixel API never blocks the
    next provider.
    """

    def __init__(self, store_settings: dict[str, Any] | None) -> None:
        self._cfg = (store_settings or {}).get("analytics") or {}

    @property
    def enabled_providers(self) -> list[str]:
        """Names of providers with non-empty credentials.

        Meta CAPI is intentionally excluded — it ships via the Celery
        task (`tasks.meta_capi_send_event`) enqueued from
        `track_page_view`. Routing it through both paths would
        double-fire every Purchase event.
        """
        out: list[str] = []
        if self._has_creds("ga4", "measurement_id", "api_secret"):
            out.append("ga4")
        if self._has_creds("tiktok", "pixel_id", "access_token"):
            out.append("tiktok")
        return out

    def _has_creds(self, key: str, *required: str) -> bool:
        cfg = self._cfg.get(key) or {}
        if not isinstance(cfg, dict):
            return False
        return all(cfg.get(r) for r in required)

    async def dispatch(self, event: AnalyticsEvent) -> list[DispatchResult]:
        """Fan an event out to all enabled providers.

        Returns a list of per-provider results. Skipped providers
        (no config) are not in the list. Errors are caught + logged;
        the aggregate result still surfaces them so the merchant can
        inspect via a future "delivery log" UI.
        """
        results: list[DispatchResult] = []
        for provider in self.enabled_providers:
            try:
                if provider == "ga4":
                    await self._send_ga4(event)
                elif provider == "tiktok":
                    await self._send_tiktok(event)
                results.append(DispatchResult(provider=provider, delivered=True))
            except Exception as exc:  # pragma: no cover — best-effort
                logger.warning(
                    "analytics_dispatch_failed",
                    extra={
                        "provider": provider,
                        "event": event.event_name,
                        "error": str(exc),
                    },
                )
                results.append(
                    DispatchResult(provider=provider, delivered=False, error=str(exc))
                )
        return results

    # ─── Provider integrations (v1: documented + stubbed) ──────────

    async def _send_ga4(self, event: AnalyticsEvent) -> None:
        """POST to GA4 Measurement Protocol.

        Endpoint: https://www.google-analytics.com/mp/collect
                  ?measurement_id=<id>&api_secret=<secret>

        v1 logs the dispatch; the actual HTTP POST is wired via the
        platform's existing httpx client when the merchant hub ships
        the config UI. Until then, enabling GA4 in store settings
        produces a log line per event without sending real traffic —
        avoids spurious data in the merchant's GA4 property when
        their setup is incomplete.
        """
        cfg = self._cfg.get("ga4", {})
        logger.info(
            "ga4_event",
            extra={
                "measurement_id": cfg.get("measurement_id"),
                "event_name": event.event_name,
                "client_id": event.client_id,
                # Don't log api_secret. logger.info auto-redacts at the
                # observability layer but be explicit anyway.
            },
        )

    async def _send_tiktok(self, event: AnalyticsEvent) -> None:
        """POST to TikTok Events API.

        Endpoint: https://business-api.tiktok.com/open_api/v1.3/event/track/

        v1 stub; same pattern as GA4 / Meta.
        """
        cfg = self._cfg.get("tiktok", {})
        logger.info(
            "tiktok_event",
            extra={
                "pixel_id": cfg.get("pixel_id"),
                "event_name": event.event_name,
            },
        )
