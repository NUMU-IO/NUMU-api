"""Audit logging middleware — logs access to endpoints that handle personal data.

Satisfies the Shopify Protected Customer Data requirement:
  "Do you log access to personal data?"

Logged fields (never logs the actual personal data):
  - timestamp (UTC)
  - request_id
  - method + path
  - source IP (hashed for privacy)
  - authenticated identity (internal-key source header)
  - response status code
  - latency
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Sequence

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("numu.audit")

# Path prefixes that touch personal / protected customer data
_PII_PREFIXES: Sequence[str] = (
    "/api/v1/shopify/",  # All Shopify endpoints (risk, orders, settings …)
    "/api/v1/customers",  # Customer endpoints
    "/api/v1/orders",  # Order endpoints
)


def _contains_pii(path: str) -> bool:
    return any(path.startswith(p) for p in _PII_PREFIXES)


def _hash_ip(ip: str) -> str:
    """One-way hash of the IP so we can correlate requests without storing PII."""
    return hashlib.sha256(ip.encode()).hexdigest()[:12]


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Emits a structured audit log line for every request that accesses PII-bearing endpoints."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if not _contains_pii(path):
            return await call_next(request)

        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

        request_id = getattr(request.state, "request_id", "-")
        source = request.headers.get("x-source", "unknown")
        client_ip = request.client.host if request.client else "unknown"

        logger.info(
            "AUDIT req_id=%s method=%s path=%s source=%s ip_hash=%s status=%s latency_ms=%s",
            request_id,
            request.method,
            path,
            source,
            _hash_ip(client_ip),
            response.status_code,
            elapsed_ms,
        )

        return response
