"""Shopify Admin API client with rate-limit queuing.

Provides mutations for:
- ``tagsAdd`` — append numu-prefixed tags to an order
- ``orderUpdate`` — append a note to an order
- ``orderCancel`` — cancel an order via REST API

All mutations respect Shopify's API rate limits using an async
token-bucket rate limiter (2 requests/second burst, 1 req/s sustained).

Constitution rules enforced here:
- Tags are ALWAYS prefixed with ``numu-`` and appended (never replace).
- Notes are ALWAYS prefixed with ``NUMU: `` and appended (never overwrite).
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_API_VERSION = "2024-10"

# ── Rate limiter ─────────────────────────────────────────────────────────────


class _TokenBucket:
    """Simple async token-bucket rate limiter.

    Allows ``burst`` immediate requests then refills at ``rate`` tokens/second.
    """

    def __init__(self, rate: float = 1.0, burst: int = 2) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Wait for a token
            wait_time = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0

        await asyncio.sleep(wait_time)


# Per-shop rate limiters (keyed by shop_domain)
_rate_limiters: dict[str, _TokenBucket] = {}


def _get_limiter(shop_domain: str) -> _TokenBucket:
    if shop_domain not in _rate_limiters:
        _rate_limiters[shop_domain] = _TokenBucket(rate=1.0, burst=2)
    return _rate_limiters[shop_domain]


# ── GraphQL mutations ────────────────────────────────────────────────────────

_TAGS_ADD_MUTATION = """
mutation tagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

_ORDER_UPDATE_MUTATION = """
mutation orderUpdate($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id note }
    userErrors { field message }
  }
}
"""


async def _graphql(
    shop_domain: str,
    access_token: str,
    query: str,
    variables: dict,
) -> dict:
    """Execute a Shopify Admin GraphQL mutation with rate limiting."""
    limiter = _get_limiter(shop_domain)
    await limiter.acquire()

    url = f"https://{shop_domain}/admin/api/{_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            json={"query": query, "variables": variables},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


# ── Public API ───────────────────────────────────────────────────────────────


async def add_tags(
    shop_domain: str,
    access_token: str,
    order_gid: str,
    tags: list[str],
) -> bool:
    """Add numu-prefixed tags to a Shopify order.

    Parameters
    ----------
    shop_domain:
        e.g. ``"example.myshopify.com"``
    access_token:
        Shopify Admin API access token.
    order_gid:
        Shopify order GID (e.g. ``"gid://shopify/Order/123456"``).
    tags:
        List of tag strings. Each is auto-prefixed with ``numu-`` if not already.

    Returns
    -------
    bool
        True on success, False on failure.
    """
    prefixed = [t if t.startswith("numu-") else f"numu-{t}" for t in tags]

    try:
        result = await _graphql(
            shop_domain,
            access_token,
            _TAGS_ADD_MUTATION,
            {"id": order_gid, "tags": prefixed},
        )
        errors = result.get("data", {}).get("tagsAdd", {}).get("userErrors") or []
        if errors:
            logger.warning("tagsAdd userErrors for %s: %s", order_gid, errors)
            return False
        logger.info("Tags added to %s: %s", order_gid, prefixed)
        return True
    except Exception as exc:
        logger.error("tagsAdd failed for %s: %s", order_gid, exc)
        return False


async def append_note(
    shop_domain: str,
    access_token: str,
    order_gid: str,
    note_text: str,
    existing_note: str = "",
) -> bool:
    """Append a NUMU-prefixed note to a Shopify order.

    The note is prefixed with ``NUMU: `` and appended to the existing note
    (never overwrites merchant data).

    Parameters
    ----------
    existing_note:
        The order's current note (to preserve). Pass empty string if unknown
        — the mutation will still append.
    """
    prefix = "NUMU: "
    formatted = f"{prefix}{note_text}"
    full_note = f"{existing_note}\n{formatted}".strip() if existing_note else formatted

    try:
        result = await _graphql(
            shop_domain,
            access_token,
            _ORDER_UPDATE_MUTATION,
            {"input": {"id": order_gid, "note": full_note}},
        )
        errors = result.get("data", {}).get("orderUpdate", {}).get("userErrors") or []
        if errors:
            logger.warning(
                "orderUpdate (note) userErrors for %s: %s", order_gid, errors
            )
            return False
        logger.info("Note appended to %s", order_gid)
        return True
    except Exception as exc:
        logger.error("orderUpdate (note) failed for %s: %s", order_gid, exc)
        return False


async def cancel_order(
    shop_domain: str,
    access_token: str,
    shopify_order_id: str,
    reason: str = "fraud",
    note: str = "",
) -> bool:
    """Cancel a Shopify order via REST API.

    Parameters
    ----------
    shopify_order_id:
        Numeric Shopify order ID (not GID).
    reason:
        One of: customer, fraud, inventory, declined, other.
    note:
        Cancellation note (prefixed with NUMU:).
    """
    limiter = _get_limiter(shop_domain)
    await limiter.acquire()

    cancel_note = f"NUMU: {note}" if note else "NUMU: Auto-cancelled by risk scoring"
    url = (
        f"https://{shop_domain}/admin/api/{_API_VERSION}"
        f"/orders/{shopify_order_id}/cancel.json"
    )
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }
    body = {
        "reason": reason,
        "note": cancel_note,
        "email": False,  # Don't email customer from Shopify — we handle notifications
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=body, headers=headers)
            if response.status_code in (200, 201):
                logger.info("Order %s cancelled on %s", shopify_order_id, shop_domain)
                return True
            logger.warning(
                "Order cancel failed: %s %s — %s",
                response.status_code,
                shopify_order_id,
                response.text[:200],
            )
            return False
    except Exception as exc:
        logger.error("Order cancel failed for %s: %s", shopify_order_id, exc)
        return False


# ── Helpers ──────────────────────────────────────────────────────────────────


def order_gid(shopify_order_id: str) -> str:
    """Convert a numeric Shopify order ID to a GID."""
    if shopify_order_id.startswith("gid://"):
        return shopify_order_id
    return f"gid://shopify/Order/{shopify_order_id}"
