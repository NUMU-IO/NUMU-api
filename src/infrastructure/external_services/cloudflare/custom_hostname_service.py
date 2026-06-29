"""Cloudflare for SaaS — custom hostname (BYO custom domain) provisioning.

When a merchant connects their own domain (e.g. `shop.brand.com`) we register
it as a Cloudflare *custom hostname* on the `numueg.app` zone. Cloudflare then:
  - issues + auto-renews a DV TLS cert for that hostname, and
  - routes its traffic to our Fallback Origin (`origin.numueg.app` → storefront),
    preserving the original Host header so the storefront's `/store-by-domain`
    lookup resolves the right store.

The merchant only has to add ONE DNS record at their registrar:
    CNAME  shop.brand.com  ->  origin.numueg.app

DCV uses the `http` method, so once that CNAME points at Cloudflare the cert
validates automatically — no TXT record juggling for the merchant.

Unlike `CloudflareDNSService` (best-effort, never raises), failures here are
surfaced to the merchant: connecting a domain is an explicit, interactive
action and a silent failure would leave them staring at a domain that never
goes live. Callers catch `CloudflareCustomHostnameError` and map it to an HTTP
error.

Reuses the same `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ZONE_ID` settings as the
subdomain DNS automation. The token additionally needs the
*SSL and Certificates: Edit* permission for custom hostnames.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)

CF_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareCustomHostnameError(Exception):
    """Raised on any Cloudflare custom-hostname API failure.

    `message` is safe to surface to the merchant (CF's own error text);
    `errors` carries the raw CF error array for logs.
    """

    def __init__(self, message: str, errors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []


class CloudflareCustomHostnameService:
    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def is_enabled(self) -> bool:
        """Custom-domain provisioning requires a token + zone. The fallback
        origin itself is configured once in the CF dashboard, not per call."""
        return bool(settings.cloudflare_api_token and settings.cloudflare_zone_id)

    @property
    def fallback_target(self) -> str:
        """The CNAME target merchants point their domain at (our fallback
        origin hostname)."""
        return settings.custom_domain_fallback_target

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                base_url=CF_API_BASE,
                headers={
                    "Authorization": f"Bearer {settings.cloudflare_api_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _zone_path(self, *parts: str) -> str:
        base = f"/zones/{settings.cloudflare_zone_id}/custom_hostnames"
        return "/".join([base, *parts]) if parts else base

    @staticmethod
    def _normalize(result: dict[str, Any]) -> dict[str, Any]:
        """Flatten a CF custom_hostname object into the shape we persist.

        CF nests SSL state under `ssl` and exposes two ownership-verification
        blocks (TXT-style `ownership_verification` and HTTP-style
        `ownership_verification_http`). We pass both through so the hub can
        show whichever the merchant needs; with the `http` DCV method the
        CNAME alone is usually enough.
        """
        ssl = result.get("ssl") or {}
        return {
            "cf_id": result.get("id"),
            "hostname": result.get("hostname"),
            # Hostname routing status: pending | active | ...
            "status": result.get("status"),
            # Cert status: pending_validation | pending_issuance | active | ...
            "ssl_status": ssl.get("status"),
            "ssl_validation_errors": ssl.get("validation_errors") or [],
            "ownership_verification": result.get("ownership_verification") or {},
            "ownership_verification_http": (
                result.get("ownership_verification_http") or {}
            ),
            "verification_errors": result.get("verification_errors") or [],
        }

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> dict[str, Any]:
        client = await self._get_client()
        try:
            resp = await client.request(method, path, json=json)
        except httpx.HTTPError as e:
            logger.warning("cf_custom_hostname_transport_error", error=str(e))
            raise CloudflareCustomHostnameError(
                "Couldn't reach Cloudflare. Please try again in a moment."
            ) from e

        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except ValueError:
            pass

        if resp.status_code >= 400 or not body.get("success", False):
            errors = body.get("errors") or []
            msg = errors[0].get("message") if errors else f"HTTP {resp.status_code}"
            logger.warning(
                "cf_custom_hostname_api_error",
                status=resp.status_code,
                errors=errors,
            )
            raise CloudflareCustomHostnameError(
                f"Cloudflare rejected the request: {msg}", errors=errors
            )

        return body.get("result") or {}

    async def create(self, hostname: str) -> dict[str, Any]:
        """Register a custom hostname; CF starts cert issuance immediately.

        Idempotency: CF returns the existing object (200) if the hostname is
        already registered on the zone, so a re-connect of the same domain is
        safe and just re-reads its current status.
        """
        result = await self._request(
            "POST",
            self._zone_path(),
            json={
                "hostname": hostname,
                "ssl": {
                    "method": "http",
                    "type": "dv",
                    "settings": {"min_tls_version": "1.2"},
                    # Auto-bundle so the cert chains correctly on older clients.
                    "bundle_method": "ubiquitous",
                },
            },
        )
        logger.info(
            "cf_custom_hostname_created", hostname=hostname, cf_id=result.get("id")
        )
        return self._normalize(result)

    async def get(self, cf_id: str) -> dict[str, Any]:
        """Fetch current status for a registered custom hostname."""
        result = await self._request("GET", self._zone_path(cf_id))
        return self._normalize(result)

    async def delete(self, cf_id: str) -> None:
        """Remove a custom hostname (and its cert) from the zone.

        Treats a 404/already-gone as success so disconnect is idempotent.
        """
        try:
            await self._request("DELETE", self._zone_path(cf_id))
        except CloudflareCustomHostnameError as e:
            if any(str(err.get("code")) in {"1436", "1437"} for err in e.errors):
                # 1436/1437 = custom hostname not found — already gone.
                return
            raise
        logger.info("cf_custom_hostname_deleted", cf_id=cf_id)


# Singleton instance (mirrors cloudflare_dns_service).
cloudflare_custom_hostname_service = CloudflareCustomHostnameService()
