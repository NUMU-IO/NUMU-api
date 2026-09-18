"""Replay-safe writes for API clients.

An integration that POSTs an order and times out has no way to know whether
the order exists. Retrying creates a second one; not retrying may lose it.
Both answers are wrong, which is why every serious API takes an idempotency
key — the client picks one per logical operation, and a repeat of the same
key returns the first response instead of doing the work again.

Scope is deliberately narrow: token-authenticated writes under
``/api/v1/stores/…`` that opt in by sending the header. Nothing else changes
behaviour, so the storefront, the hub and the existing bespoke checkout key
are untouched.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)

HEADER = "Idempotency-Key"
_METHODS = frozenset({"POST", "PUT", "PATCH"})
#: Long enough to cover a client's retry policy, short enough that a key can
#: be reused next week without surprising anyone.
_TTL_SECONDS = 24 * 60 * 60
#: A request still in flight holds the slot only briefly — a crashed worker
#: must not lock the key out for a day.
_INFLIGHT_TTL_SECONDS = 90
#: Bodies above this are not worth replaying; the write still happens.
_MAX_REPLAY_BYTES = 256 * 1024

_cache = RedisCacheService()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Replay the first response for a repeated ``Idempotency-Key``."""

    async def dispatch(self, request: Request, call_next):
        key = request.headers.get(HEADER)
        if (
            not key
            or request.method not in _METHODS
            or not request.url.path.startswith("/api/v1/stores/")
        ):
            return await call_next(request)

        # The key is scoped to the caller and the exact operation: two clients
        # sharing a key must not read each other's responses, and the same key
        # on a different endpoint is a different operation.
        auth = request.headers.get("authorization", "")
        slot = (
            "idem:"
            + hashlib.sha256(
                f"{auth}|{request.method}|{request.url.path}|{key}".encode()
            ).hexdigest()
        )

        # Claim the slot atomically: two identical requests racing (the retry
        # arriving while the first is still running) must not both execute.
        try:
            claimed = await _cache.set_if_absent(
                slot, {"status": "in_flight"}, expire=_INFLIGHT_TTL_SECONDS
            )
        except Exception:
            # Redis down: the write must still go through. Losing replay
            # protection is bad; refusing every write is worse.
            logger.debug("idempotency_cache_unavailable")
            return await call_next(request)

        if not claimed:
            stored = await _cache.get(slot) or {"status": "in_flight"}
            if stored.get("status") == "in_flight":
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={
                        "detail": (
                            "A request with this Idempotency-Key is still in "
                            "progress. Retry in a moment."
                        )
                    },
                )
            return JSONResponse(
                status_code=stored["code"],
                content=stored["body"],
                headers={"Idempotent-Replay": "true"},
            )

        response = await call_next(request)

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Only a completed, replayable JSON response is worth storing. A 5xx
        # is not an answer, and the client should be free to retry it.
        replayable = (
            response.status_code < 500
            and len(body) <= _MAX_REPLAY_BYTES
            and response.headers.get("content-type", "").startswith("application/json")
        )
        try:
            if replayable:
                await _cache.set(
                    slot,
                    {"code": response.status_code, "body": json.loads(body)},
                    expire=_TTL_SECONDS,
                )
            else:
                await _cache.delete(slot)
        except Exception:
            logger.debug("idempotency_store_failed", path=request.url.path)

        return JSONResponse(
            status_code=response.status_code,
            content=json.loads(body) if body else None,
            headers={
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-length", "content-type")
            },
        )
