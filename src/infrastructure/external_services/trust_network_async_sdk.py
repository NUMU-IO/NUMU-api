"""Vendored NUMU Trust Network async SDK client (single-file).

VENDORED from ``numu-trust-network/packages/trust-sdk-python`` @ b9dc502
(``async_client.py`` + the error classes from ``client.py``), self-contained so
NUMU-api needs no git dependency on the private repo. Replace this file with
``pip install trust-network-sdk`` once the SDK is published to PyPI — do not
extend it here; change the SDK upstream and re-vendor.

What it adds over raw httpx: bearer auth, auto ``Idempotency-Key`` on writes,
retry with exponential backoff on transient failures (429 / 5xx / network),
and typed errors.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

# Transient statuses worth retrying. 4xx (except 429) are caller errors — never retried.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class TrustNetworkError(Exception):
    """Base error for all SDK failures (including exhausted retries / transport)."""


class APIError(TrustNetworkError):
    """The API returned a non-2xx response."""

    def __init__(
        self, status_code: int, message: str, *, request_id: str | None = None
    ) -> None:
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.request_id = request_id


class AuthError(APIError):
    """401/403 — bad key or missing scope."""


class RateLimitError(APIError):
    """429 — rate limit exhausted (after retries)."""


class AsyncTrustNetworkClient:
    """Asynchronous client. Use as an async context manager or call ``aclose()``."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.trustnetwork.example",
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncTrustNetworkClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ── endpoints ──

    async def decide(
        self, order: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Score + decide a COD order. ``order`` matches the ``DecisionRequest`` schema."""
        return await self._request(
            "POST", "/v1/decisions", json=order, idempotency_key=idempotency_key
        )

    async def get_decision(self, decision_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/decisions/{decision_id}")

    async def record_event(
        self, event: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Contribute an outcome to the graph (idempotent via the event's ``dedup_key``)."""
        return await self._request(
            "POST", "/v1/events", json=event, idempotency_key=idempotency_key
        )

    async def get_buyer_reputation(self, token: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/reputation/buyer/{token}")

    # ── transport: retry + backoff + error mapping ──

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if method == "POST":
            headers["Idempotency-Key"] = idempotency_key or f"idk_{uuid.uuid4().hex}"
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method, path, json=json, headers=headers
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise TrustNetworkError(
                        f"transport error after {attempt + 1} attempt(s): {exc}"
                    ) from exc
                await self._sleep(self._backoff_base * (2**attempt))
                continue
            if response.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                await self._sleep(self._backoff_base * (2**attempt))
                continue
            return self._handle(response)
        raise TrustNetworkError("unreachable: retry loop exited without returning")

    def _handle(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_success:
            data = response.json() if response.content else {}
            return data if isinstance(data, dict) else {"data": data}
        request_id = response.headers.get("X-Request-ID")
        message = self._error_message(response)
        if response.status_code in (401, 403):
            raise AuthError(response.status_code, message, request_id=request_id)
        if response.status_code == 429:
            raise RateLimitError(response.status_code, message, request_id=request_id)
        raise APIError(response.status_code, message, request_id=request_id)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
        except Exception:
            return response.text or response.reason_phrase
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])
        return str(data)
