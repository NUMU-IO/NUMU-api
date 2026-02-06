"""Response compression middleware.

Provides gzip compression for API responses when Nginx is bypassed
(local development, direct Uvicorn access). In staging/production behind
Nginx, Nginx handles compression; the middleware's Content-Encoding header
prevents double-compression (both gzip and brotli modules check for it).

Uses Starlette's proven GzipMiddleware with:
- minimum_size=500: Skip compressing small response bodies
- compresslevel=6: Good balance of compression ratio vs CPU cost

ETag coordination:
    Compression changes the response body, so strong ETags become invalid.
    If ETag generation is added in the future, use weak ETags (W/"...")
    per RFC 7232 §2.1. Currently no ETag generation exists at the app layer.

References:
    - Starlette GzipMiddleware: https://www.starlette.io/middleware/#gzipmiddleware
    - RFC 7231 §3.1.2.2 (Content-Encoding)
    - RFC 7232 §2.1 (Weak vs Strong ETags)
"""

import logging

from starlette.middleware.gzip import GzipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class CompressionMiddleware(GzipMiddleware):
    """Gzip compression middleware with SSE bypass.

    Wraps Starlette's GzipMiddleware and adds a guard against
    compressing Server-Sent Events (text/event-stream) responses,
    which must be delivered as a raw byte stream without encoding.

    Starlette's GzipMiddleware already handles:
    - Checking Accept-Encoding for gzip support
    - Setting Content-Encoding: gzip
    - Adding Vary: Accept-Encoding
    - Skipping responses that already have Content-Encoding set
    - Respecting minimum_size threshold
    """

    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 500,
        compresslevel: int = 6,
    ) -> None:
        super().__init__(
            app,
            minimum_size=minimum_size,
            compresslevel=compresslevel,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process request, bypassing compression for SSE connections."""
        if scope["type"] == "http":
            # Check if client requests SSE — compression breaks streaming
            headers = dict(scope.get("headers", []))
            accept = headers.get(b"accept", b"").decode("latin-1")
            if "text/event-stream" in accept:
                await self.app(scope, receive, send)
                return

        await super().__call__(scope, receive, send)
