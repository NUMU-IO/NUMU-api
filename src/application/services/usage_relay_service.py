"""Usage-overage relay service (backend-004).

Closes the billing loop with the Shopify-app companion repo. When a
merchant exceeds their tier's WhatsApp/SMS verification cap, this
service POSTs a usage event to the Shopify-app's
`/api/billing/usage-record` endpoint, which translates it into a
Shopify Billing API `appUsageRecordCreate` mutation.

See specs/backend-004-usage-overage-relay/spec.md.

Constitution alignment:
  - Numu API contract is versioned: the request body shape
    `{shop_domain, amount_cents, description, idempotency_key}` matches
    the Shopify-app's expected schema at
    `numu-payments-intelligence/app/routes/api.billing.usage-record.tsx`.
    Any breaking change requires a contract version bump.
  - Secret hygiene: the `X-Internal-Key` header is sourced from
    settings, never inlined.
  - Spec-First: every acceptance scenario has a test in
    tests/api/test_usage_relay.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

# 5-second timeout matches the Shopify-app's expected GraphQL latency
# for a single appUsageRecordCreate mutation. Above this we treat the
# call as a transient failure and surface RelayUnavailable so the
# calling Celery task can retry.
_HTTP_TIMEOUT_S = 5.0

_USAGE_PATH = "/api/billing/usage-record"


# ---------------------------------------------------------------------------
# Result + error types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelayResult:
    """Typed outcome of a usage-record relay call.

    Mirrors the Shopify-app's UsageRecordResult discriminated union at
    `app/lib/billing/usage.server.ts`.
    """

    recorded: bool
    capped: bool
    shopify_usage_record_id: str | None = None
    reason: str | None = None
    detail: str | None = None


class RelayConfigError(RuntimeError):
    """Deployment configuration is broken: missing URL, missing key,
    or 401 from the Shopify-app side. Should page on-call — not
    something a Celery retry will fix."""


class RelayUnavailable(RuntimeError):
    """Transient failure: network error, timeout, or 5xx from the
    Shopify-app. The calling task should retry with backoff."""


class RelayInvalidPayload(RuntimeError):
    """The Shopify-app rejected the body (422). Programmer error;
    check the spec contract and the request shape."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class UsageRelayService:
    """POSTs verification-overage events to the Shopify-app.

    Stateless. Reads `shopify_app_url` and `shopify_internal_key` from
    settings on each call (in case they're rotated mid-run; not common
    but cheap to support).
    """

    def __init__(
        self,
        client_factory: type[httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        # Injectable factory so tests can swap in a MockTransport.
        self._client_factory = client_factory

    async def post_usage(
        self,
        *,
        shop_domain: str,
        amount_cents: int,
        description: str,
        idempotency_key: str,
    ) -> RelayResult:
        """Send a usage event to the Shopify-app.

        Idempotency is the Shopify-app's responsibility — we trust the
        provided key and forward. Calling this twice with the same key
        yields exactly one Shopify usage-record.

        Raises:
            RelayConfigError — missing URL/key, or 401 from upstream.
            RelayInvalidPayload — 422 from upstream (schema mismatch).
            RelayUnavailable — network error, timeout, or 5xx.
        """
        settings = get_settings()
        base_url = settings.shopify_app_url.rstrip("/")
        internal_key = settings.shopify_internal_key

        if not base_url:
            raise RelayConfigError(
                "shopify_app_url is not configured; backend-004 relay disabled"
            )
        if not internal_key:
            raise RelayConfigError(
                "shopify_internal_key is not configured; backend-004 relay disabled"
            )

        url = f"{base_url}{_USAGE_PATH}"
        body = {
            "shop_domain": shop_domain,
            "amount_cents": amount_cents,
            "description": description,
            "idempotency_key": idempotency_key,
        }
        headers = {
            "X-Internal-Key": internal_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with self._client_factory(timeout=_HTTP_TIMEOUT_S) as client:
                response = await client.post(url, json=body, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            logger.warning(
                "[usage-relay] network failure posting to Shopify-app: %s",
                e,
            )
            raise RelayUnavailable(str(e)) from e

        # Status interpretation per spec FR-004.
        if response.status_code == 200:
            data = response.json()
            return RelayResult(
                recorded=bool(data.get("recorded", False)),
                capped=bool(data.get("capped", False)),
                shopify_usage_record_id=data.get("shopifyUsageRecordId"),
                reason=data.get("reason"),
                detail=data.get("detail"),
            )
        if response.status_code == 401:
            raise RelayConfigError(
                "Shopify-app rejected the X-Internal-Key (401). Check that "
                "shopify_internal_key matches NUMU_API_INTERNAL_KEY on the "
                "Shopify-app side."
            )
        if response.status_code == 422:
            # Programmer error — schema mismatch. Surface the upstream
            # detail so the dev can fix it.
            raise RelayInvalidPayload(
                f"Shopify-app rejected the body: {response.text[:500]}"
            )
        # 4xx (other) and 5xx → treat as transient.
        logger.warning(
            "[usage-relay] non-200 from Shopify-app: %s %s",
            response.status_code,
            response.text[:500],
        )
        raise RelayUnavailable(f"http_{response.status_code}")
