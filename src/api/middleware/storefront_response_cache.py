"""Whole-response cache for the storefront's hottest public reads.

Every storefront page view fires ~8 API reads at once, and the API runs on one
event loop. A read that was already cached one layer down (product list,
categories, theme) still cost 20-38 ms of CPU: routing, a DB session with its
setup queries, pydantic validation and gzip. Under bursts those milliseconds
queue up into 1-2 s p95s.

This middleware stores the finished response (status, headers, compressed
body) and replays it before any of that runs. It sits inside the rate limiter,
CORS and logging, so those still apply to every request.

Only anonymous GETs on a short allow-list are cached, for 60 seconds, and the
existing invalidation paths (product, category, theme and menu writes) drop a
store's entries at once via ``bust_storefront_responses``. Anything
unusual bypasses it: search, sparse fieldsets, preview/draft, conditional
requests, an Authorization header, a response that sets a cookie. Redis
trouble is a miss, never an error.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode

from redis.exceptions import RedisError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.infrastructure.cache.response_cache import (
    TTL_SECONDS,
    response_cache_redis,
    store_prefix,
)

_UUID = r"[0-9a-fA-F-]{36}"

# path regex -> query params that may vary the response (anything else bypasses)
_CACHEABLE: list[tuple[re.Pattern[str], frozenset[str]]] = [
    (
        re.compile(rf"^/api/v1/storefront/store/({_UUID})/products$"),
        frozenset({"page", "limit", "category_id"}),
    ),
    (re.compile(rf"^/api/v1/storefront/store/({_UUID})/products/[^/]+$"), frozenset()),
    (re.compile(rf"^/api/v1/storefront/store/({_UUID})/categories$"), frozenset()),
    (re.compile(rf"^/api/v1/storefront/store/({_UUID})/menus$"), frozenset()),
    (re.compile(rf"^/api/v1/storefront/theme/({_UUID})$"), frozenset()),
]
_BYPASS_HEADERS = (b"authorization", b"x-preview-token", b"if-none-match")
# Per-request or per-client headers never replayed from the cache.
_DROP_HEADERS = {b"set-cookie", b"date", b"x-request-id", b"x-response-time", b"server"}


def _cache_key(scope: Scope) -> str | None:
    if scope["type"] != "http" or scope["method"] != "GET":
        return None
    path: str = scope["path"]
    for pattern, allowed in _CACHEABLE:
        m = pattern.match(path)
        if not m:
            continue
        params = sorted(
            parse_qsl(
                scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True
            )
        )
        if any(k not in allowed for k, _ in params):
            return None
        headers = dict(scope.get("headers") or [])
        if any(h in headers for h in _BYPASS_HEADERS):
            return None
        enc = "gzip" if b"gzip" in headers.get(b"accept-encoding", b"") else "id"
        return f"{store_prefix(m.group(1))}:{enc}:{path}?{urlencode(params)}"
    return None


class StorefrontResponseCacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        key = _cache_key(scope)
        if key is None:
            await self.app(scope, receive, send)
            return

        try:
            hit = await response_cache_redis().get(key)
        except RedisError:
            hit = None
        if hit:
            meta, _, body = hit.partition(b"\n")
            status, headers = json.loads(meta)
            await send({
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (k.encode("latin-1"), v.encode("latin-1")) for k, v in headers
                ]
                + [(b"x-numu-cache", b"hit")],
            })
            await send({"type": "http.response.body", "body": body})
            return

        start: Message | None = None
        chunks: list[bytes] = []
        cacheable = True

        async def capture(message: Message) -> None:
            nonlocal start, cacheable
            if message["type"] == "http.response.start":
                start = message
                if message["status"] != 200 or any(
                    k.lower() == b"set-cookie" for k, _ in message.get("headers", [])
                ):
                    cacheable = False
            elif message["type"] == "http.response.body" and cacheable:
                chunks.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, capture)

        if not cacheable or start is None:
            return
        headers = [
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in start.get("headers", [])
            if k.lower() not in _DROP_HEADERS
        ]
        try:
            await response_cache_redis().set(
                key,
                json.dumps([start["status"], headers]).encode()
                + b"\n"
                + b"".join(chunks),
                ex=TTL_SECONDS,
            )
        except RedisError:
            pass
