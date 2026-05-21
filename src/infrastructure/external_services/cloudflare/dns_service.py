"""Cloudflare DNS automation for per-env store subdomains.

Test/staging stacks need an `A <store><suffix>.numueg.app -> droplet IP` record
on every store creation. Prod is handled by the `* CNAME -> Heroku` fallthrough
and does not call this.
"""

import httpx

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)

CF_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareDNSError(Exception):
    pass


class CloudflareDNSService:
    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def is_enabled(self) -> bool:
        return bool(
            settings.cloudflare_auto_dns_enabled
            and settings.cloudflare_api_token
            and settings.cloudflare_zone_id
            and settings.droplet_ip
        )

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

    async def ensure_store_subdomain(self, subdomain: str) -> bool:
        """Create A record for store subdomain if missing. Best-effort.

        Returns True on success or no-op, False on failure. Never raises —
        a DNS failure must not roll back the store creation transaction.
        """
        if not self.is_enabled:
            return True

        name = subdomain
        fqdn = f"{name}.numueg.app"
        try:
            client = await self._get_client()
            zone = settings.cloudflare_zone_id

            existing = await client.get(
                f"/zones/{zone}/dns_records",
                params={"name": fqdn, "type": "A"},
            )
            existing.raise_for_status()
            results = existing.json().get("result", [])
            if results:
                logger.info("cloudflare.dns.exists", extra={"fqdn": fqdn})
                return True

            create = await client.post(
                f"/zones/{zone}/dns_records",
                json={
                    "type": "A",
                    "name": name,
                    "content": settings.droplet_ip,
                    "ttl": 1,
                    "proxied": True,
                    "comment": f"Auto-created by NUMU-api for store {subdomain}",
                },
            )
            if create.status_code >= 400:
                logger.error(
                    "cloudflare.dns.create_failed",
                    extra={
                        "fqdn": fqdn,
                        "status": create.status_code,
                        "body": create.text[:500],
                    },
                )
                return False
            logger.info("cloudflare.dns.created", extra={"fqdn": fqdn})
            return True
        except Exception as e:
            logger.exception(
                "cloudflare.dns.exception", extra={"fqdn": fqdn, "error": str(e)}
            )
            return False


cloudflare_dns_service = CloudflareDNSService()
