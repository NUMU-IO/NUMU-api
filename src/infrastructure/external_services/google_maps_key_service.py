"""Google Maps browser-key referrer allowlist maintenance.

The storefront's checkout location picker loads the Maps JS API with a browser
key restricted by HTTP referrer. Google checks that list against the *page's*
URL, so it has to name every host a checkout is ever served from — including
every merchant's own domain. Nothing server-side can influence the check.

Miss one and the failure is quiet: Maps answers `RefererNotAllowedMapError`,
`gm_authFailure` fires, and the picker degrades to manual address entry. The
checkout still completes, so no error is reported and no order is lost — the
merchant simply has a worse checkout than the one they were sold, indefinitely.
That is exactly what happened to the first custom domain to go live.

This service closes that gap by appending the domain to the key's allowlist on
the same edge that activates it. It is deliberately best-effort at the call
sites: a Google outage must never block a merchant's domain from going live,
because the picker's fallback is a working checkout and a blocked activation
is not.

Requires a service account with `apikeys.keys.get` and `apikeys.keys.update`
on the project owning the key. Unset settings → `is_enabled` is False and every
method is a no-op, leaving the allowlist a manual console job.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

APIKEYS_API_BASE = "https://apikeys.googleapis.com/v2"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Read-modify-write on a single shared key races with itself: two domains
# activating at once both read the old list and the second write drops the
# first domain. Google returns the key's etag and rejects a stale one, so a
# lost update surfaces as a conflict we can retry rather than a silently
# missing referrer.
_MAX_CONFLICT_RETRIES = 3


class GoogleMapsKeyError(Exception):
    """Raised on an API Keys call that could not be completed."""


def referrer_patterns(domain: str) -> list[str]:
    """The allowlist entries covering a merchant domain.

    Both the apex and its subdomains: merchants point either `brand.com` or
    `shop.brand.com` at us, and which one they choose is not knowable here.
    Google matches these literally, so `https://brand.com/*` does NOT cover
    `https://www.brand.com/` — the `*.` entry is what makes `www` work.
    """
    host = domain.strip().lower().removeprefix("https://").removeprefix("http://")
    host = host.split("/", 1)[0].rstrip(".")
    if not host:
        return []
    return [f"https://{host}/*", f"https://*.{host}/*"]


class GoogleMapsKeyService:
    """Maintains the browser key's `allowedReferrers` list."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._credentials: Any | None = None

    @property
    def is_enabled(self) -> bool:
        return bool(
            settings.google_maps_key_project
            and settings.google_maps_key_id
            and settings.google_maps_key_credentials
        )

    @property
    def _key_path(self) -> str:
        return (
            f"/projects/{settings.google_maps_key_project}"
            f"/locations/global/keys/{settings.google_maps_key_id}"
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── auth ────────────────────────────────────────────────────────────

    def _load_credentials(self) -> Any:
        """Build (once) a service-account credential for the API Keys scope."""
        if self._credentials is not None:
            return self._credentials
        from google.oauth2 import service_account  # imported lazily

        try:
            info = json.loads(settings.google_maps_key_credentials or "")
        except json.JSONDecodeError as e:
            raise GoogleMapsKeyError(
                "google_maps_key_credentials is not valid JSON"
            ) from e
        self._credentials = service_account.Credentials.from_service_account_info(
            info, scopes=[CLOUD_PLATFORM_SCOPE]
        )
        return self._credentials

    async def _access_token(self) -> str:
        """Mint/refresh an access token off the event loop.

        google-auth is synchronous and refresh performs a network round trip,
        so it runs in a thread. The credential object caches the token and
        only refreshes when it is close to expiry.
        """

        def _refresh() -> str:
            from google.auth.transport.requests import Request

            creds = self._load_credentials()
            if not creds.valid:
                creds.refresh(Request())
            return str(creds.token)

        return await asyncio.to_thread(_refresh)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                base_url=APIKEYS_API_BASE,
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        client = await self._get_client()
        token = await self._access_token()
        try:
            return await client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as e:
            raise GoogleMapsKeyError(f"API Keys request failed: {e}") from e

    # ── allowlist maintenance ───────────────────────────────────────────

    async def _fetch_key(self) -> dict[str, Any]:
        resp = await self._request("GET", self._key_path)
        if resp.status_code != 200:
            raise GoogleMapsKeyError(
                f"could not read the Maps key ({resp.status_code}): {resp.text[:300]}"
            )
        return resp.json()

    async def _write_referrers(self, referrers: list[str], etag: str | None) -> bool:
        """PATCH the allowlist. False when the etag was stale (retry)."""
        body: dict[str, Any] = {
            "restrictions": {"browserKeyRestrictions": {"allowedReferrers": referrers}}
        }
        if etag:
            body["etag"] = etag
        resp = await self._request(
            "PATCH",
            self._key_path,
            params={
                "updateMask": "restrictions.browserKeyRestrictions.allowedReferrers"
            },
            json_body=body,
        )
        # 409/412 = someone else wrote first; our view of the list is stale.
        if resp.status_code in (409, 412):
            return False
        if resp.status_code not in (200, 201):
            raise GoogleMapsKeyError(
                f"could not update the Maps key ({resp.status_code}): {resp.text[:300]}"
            )
        return True

    async def _mutate(self, domain: str, *, add: bool) -> bool:
        """Read-modify-write the allowlist. True when it actually changed."""
        patterns = referrer_patterns(domain)
        if not patterns:
            return False

        for _ in range(_MAX_CONFLICT_RETRIES):
            key = await self._fetch_key()
            restrictions = key.get("restrictions") or {}
            browser = restrictions.get("browserKeyRestrictions") or {}
            current: list[str] = list(browser.get("allowedReferrers") or [])

            if add:
                missing = [p for p in patterns if p not in current]
                if not missing:
                    return False  # already authorised — don't churn the key
                updated = current + missing
            else:
                updated = [p for p in current if p not in patterns]
                if len(updated) == len(current):
                    return False

            # Refuse to write an empty allowlist. An unrestricted browser key
            # is billable by anyone who copies it out of the page source, and
            # arriving there by accident — a bad read, a key with only this
            # domain on it — is worse than leaving a stale entry behind.
            if add is False and not updated:
                logger.warning(
                    "maps_key_referrer_revoke_skipped_empty",
                    extra={"domain": domain},
                )
                return False

            if await self._write_referrers(updated, key.get("etag")):
                return True

        raise GoogleMapsKeyError(
            "the Maps key changed under us on every attempt; allowlist not updated"
        )

    async def authorize_domain(self, domain: str) -> bool:
        """Add a merchant domain to the key. True when it was newly added."""
        if not self.is_enabled:
            return False
        return await self._mutate(domain, add=True)

    async def revoke_domain(self, domain: str) -> bool:
        """Remove a merchant domain. True when something was removed."""
        if not self.is_enabled:
            return False
        return await self._mutate(domain, add=False)


google_maps_key_service = GoogleMapsKeyService()
