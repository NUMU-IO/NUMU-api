"""Google Search Console sitemap submission.

Why this exists
---------------
Every merchant storefront is its own host (``<subdomain>.numueg.app``) and ships
a correct ``/sitemap.xml`` — but a sitemap Google has never been *told about*
does nothing. URL Inspection on a live storefront collection page came back:

    Page is not indexed: URL is unknown to Google
    Discovery — Sitemaps: No referring sitemaps detected
                Referring page: None detected

IndexNow (see :mod:`nextjs_revalidation`) does not close this gap: it feeds
Bing, DuckDuckGo and Yandex. **Google does not consume IndexNow.** The Search
Console API is the only programmatic way to hand Google a sitemap, so this
module exists alongside the IndexNow ping rather than replacing it.

Setup (one-time, all of it outside this repo)
---------------------------------------------
1. Verify ``numueg.app`` in Search Console as a **Domain property** (DNS TXT via
   Cloudflare). A URL-prefix property for ``https://numueg.app/`` does *not*
   cover subdomains, so per-store sitemaps would be rejected as "outside the
   property". This is the step that makes every ``*.numueg.app`` submittable.
2. Create a GCP service account, enable the **Google Search Console API**, and
   download a JSON key.
3. In Search Console → Settings → Users and permissions, add the service
   account's ``client_email`` as an **Owner**. Anything less than Owner cannot
   submit sitemaps and the API returns 403.
4. Set the env vars below on the API box.

Env
---
``GOOGLE_SEARCH_CONSOLE_CREDENTIALS_JSON``
    Service-account key, either raw JSON or base64 of it. Unset ⇒ this module
    is inert and every call is a no-op, which is the correct behaviour for
    dev/test/stage where the hosts are not verified anyway.
``GOOGLE_SEARCH_CONSOLE_SITE_URL``
    Property identifier. Defaults to ``sc-domain:numueg.app`` (domain-property
    form). A URL-prefix property would instead be ``https://numueg.app/``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/webmasters"
API_ROOT = "https://www.googleapis.com/webmasters/v3"

_CREDENTIALS_RAW = os.getenv("GOOGLE_SEARCH_CONSOLE_CREDENTIALS_JSON", "")
SITE_URL = os.getenv("GOOGLE_SEARCH_CONSOLE_SITE_URL", "sc-domain:numueg.app")


def is_configured() -> bool:
    """True when a service-account key is present."""
    return bool(_CREDENTIALS_RAW.strip())


def _load_credentials_info() -> dict | None:
    """Parse the key from env, accepting raw JSON or base64-wrapped JSON.

    Base64 is supported because the key is a multi-line JSON blob with embedded
    ``\\n`` in the private key, which several of our deployment paths (compose
    ``.env`` files, GitHub secrets → ``echo`` into a file) mangle.
    """
    raw = _CREDENTIALS_RAW.strip()
    if not raw:
        return None

    if not raw.startswith("{"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError) as e:
            logger.warning("search_console_credentials_b64_decode_failed: %s", e)
            return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("search_console_credentials_parse_failed: %s", e)
        return None


def _fetch_access_token_sync() -> str | None:
    """Mint a bearer token. Blocking — always call via ``asyncio.to_thread``.

    google-auth's transport is synchronous (it uses ``requests`` under the
    hood), so calling ``refresh()`` on the event loop would stall every other
    request on the worker for the duration of Google's token round-trip.
    """
    info = _load_credentials_info()
    if not info:
        return None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=[SCOPE]
        )
        credentials.refresh(Request())
        return credentials.token
    except Exception as e:  # noqa: BLE001 — best-effort, never fatal
        logger.warning("search_console_token_failed: %s", e)
        return None


async def submit_sitemap(sitemap_url: str) -> bool:
    """Register one sitemap with Search Console.

    ``PUT .../sitemaps/{feedpath}`` is idempotent — re-submitting an already
    known sitemap is a 204, not an error — so callers are free to call this on
    every publish without tracking what has been submitted before.

    Never raises: a store must not fail to be created because Google is down.
    Returns True only on a confirmed 2xx.
    """
    if not is_configured():
        logger.debug("search_console_skipped: no credentials configured")
        return False

    token = await asyncio.to_thread(_fetch_access_token_sync)
    if not token:
        return False

    # Both path segments are opaque identifiers containing ':' and '/', so
    # `safe=""` is required — the default would leave '/' unescaped and the
    # sitemap URL would be read as extra path segments.
    endpoint = (
        f"{API_ROOT}/sites/{quote(SITE_URL, safe='')}"
        f"/sitemaps/{quote(sitemap_url, safe='')}"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.put(
                endpoint, headers={"Authorization": f"Bearer {token}"}
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("search_console_submit_error for %s: %s", sitemap_url, e)
        return False

    if response.status_code // 100 == 2:
        logger.info("search_console_sitemap_submitted: %s", sitemap_url)
        return True

    # 403 here almost always means the service account is not an Owner of the
    # property, or the property is URL-prefix (so a subdomain sitemap is out of
    # scope) rather than a domain property. Both are setup problems, not bugs.
    logger.warning(
        "search_console_submit_failed for %s: %s %s",
        sitemap_url,
        response.status_code,
        response.text[:200],
    )
    return False


async def submit_store_sitemap(
    subdomain: str, custom_domain: str | None = None
) -> bool:
    """Submit the sitemap for one storefront.

    A custom domain is a different property from ``numueg.app`` and will not be
    covered by ``SITE_URL``, so we only submit the subdomain host. The merchant
    verifies their own domain in their own Search Console account.
    """
    if not subdomain:
        return False
    return await submit_sitemap(f"https://{subdomain}.numueg.app/sitemap.xml")
