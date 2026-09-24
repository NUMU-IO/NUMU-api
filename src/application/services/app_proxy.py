"""App proxy: ``https://<store>/apps/<slug>/*`` fetched from the app's server.

The storefront forwards the shopper's request here; NUMU signs it with the
app's client secret (the same query-string HMAC as "Open app") and relays the
answer. The partner never sees the shopper's cookies or credentials, and the
page it returns runs in a CSP sandbox: it is served on the store's origin, so
without one its scripts could read the store's cookies and call its APIs as
the shopper.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from src.application.services.app_tokens import signed_params
from src.core.url_guard import UnsafeUrlError, assert_webhook_target

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_BYTES = 5 * 1024 * 1024
FORWARD_HEADERS = ("accept", "accept-language", "content-type", "user-agent")
CONTENT_TYPES = (
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
)
SANDBOX = "sandbox allow-scripts allow-forms allow-popups"


@dataclass(frozen=True)
class ProxyResponse:
    status: int
    body: bytes
    headers: dict[str, str]


def _error(status: int) -> ProxyResponse:
    return ProxyResponse(status, b"", {})


def _allowed_type(content_type: str) -> bool:
    base = content_type.split(";", 1)[0].strip().lower()
    return base in CONTENT_TYPES or (
        base.startswith("image/") and base != "image/svg+xml"
    )


async def forward(
    *,
    base_url: str,
    secret: str,
    store_id: str,
    slug: str,
    path: str,
    method: str,
    query: dict[str, str],
    headers: dict[str, str],
    body: bytes,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProxyResponse:
    """Relay one storefront request to the app. Never raises for the app's
    failures: 504 on timeout, 502 on anything else it gets wrong."""
    if len(body) > MAX_BYTES:
        return _error(413)
    url = base_url.rstrip("/") + "/" + quote(path.lstrip("/"), safe="/")
    try:
        await asyncio.to_thread(assert_webhook_target, url)
    except UnsafeUrlError:
        return _error(502)
    params = signed_params(
        {**query, "store_id": store_id, "path_prefix": f"/apps/{slug}"}, secret
    )
    sent = {k: v for k, v in headers.items() if k.lower() in FORWARD_HEADERS}
    try:
        async with (
            httpx.AsyncClient(
                timeout=TIMEOUT, follow_redirects=False, transport=transport
            ) as client,
            client.stream(
                method, url, params=params, headers=sent, content=body or None
            ) as res,
        ):
            chunks, size = [], 0
            async for chunk in res.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    return _error(502)
                chunks.append(chunk)
    except httpx.TimeoutException:
        return _error(504)
    except httpx.HTTPError:
        return _error(502)

    out = {"Content-Security-Policy": SANDBOX, "X-Content-Type-Options": "nosniff"}
    if res.is_redirect:
        location = res.headers.get("location", "")
        if not location.startswith("/") or location.startswith(("//", "/\\")):
            return _error(502)
        return ProxyResponse(res.status_code, b"", {**out, "Location": location})
    content_type = res.headers.get("content-type", "")
    if chunks and not _allowed_type(content_type):
        return _error(502)
    if content_type:
        out["Content-Type"] = content_type
    if res.headers.get("cache-control"):
        out["Cache-Control"] = res.headers["cache-control"]
    return ProxyResponse(res.status_code, b"".join(chunks), out)
