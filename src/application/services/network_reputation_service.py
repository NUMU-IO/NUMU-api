"""Cross-merchant network reputation service.

Shared by Shopify integration and native NUMU storefronts.
Provides Redis-cached lookups, phone hashing, and event recording on top of
``NetworkReputationRepository``.

All public functions are platform-agnostic and never raise — they fail open
(return baseline values + log warnings) so callers can use them in
fraud-filtering paths without breaking the order flow on infrastructure issues.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from src.application.use_cases.shopify.phone_hash import normalize_and_hash
from src.application.use_cases.shopify.risk_scoring_engine import compute_network_score
from src.config import get_settings

if TYPE_CHECKING:
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
    )

logger = logging.getLogger(__name__)

# Shared cache namespace — used by BOTH Shopify webhook and native storefront
# checkout. Cache payload now includes confidence_level for trust enforcement.
_NET_SCORE_CACHE_KEY = "shopify:net_score:{phone_hash}"
_NET_SCORE_CACHE_TTL = 3600  # 1 hour
_BASELINE_SCORE = 55
_BASELINE_LABEL = "new_to_network"
_BASELINE_CONFIDENCE = "low"


async def lookup_network_reputation(
    phone_hash: str | None,
    network_repo: NetworkReputationRepository,
) -> tuple[int, str, str]:
    """Look up network reputation: Redis cache → DB fallback → baseline.

    Returns ``(score, confidence_level, label)``. Never raises — returns the
    baseline (55, "low", "new_to_network") on any failure.
    """
    if not phone_hash:
        return _BASELINE_SCORE, _BASELINE_CONFIDENCE, _BASELINE_LABEL

    # 1. Try Redis cache
    try:
        from src.infrastructure.cache.redis_cache import RedisCacheService

        cache = RedisCacheService()
        key = _NET_SCORE_CACHE_KEY.format(phone_hash=phone_hash)
        cached = await cache.get(key)
        await cache.close()
        if cached is not None and isinstance(cached, dict):
            return (
                cached.get("score", _BASELINE_SCORE),
                cached.get("confidence", _BASELINE_CONFIDENCE),
                cached.get("label", _BASELINE_LABEL),
            )
    except Exception as exc:
        logger.warning("Redis network score cache lookup failed: %s", exc)

    # 2. Fallback to DB
    try:
        rep = await network_repo.get_by_phone_hash(phone_hash)
        if rep is None:
            return _BASELINE_SCORE, _BASELINE_CONFIDENCE, _BASELINE_LABEL

        score, confidence, label = compute_network_score(
            total_orders=rep.total_network_orders,
            total_rtos=rep.total_network_rtos,
            total_deliveries=rep.total_successful_deliveries,
            total_refunds=rep.total_refunds,
            contributing_store_count=rep.contributing_store_count,
        )

        # Cache the result in Redis for future lookups
        try:
            cache = RedisCacheService()
            key = _NET_SCORE_CACHE_KEY.format(phone_hash=phone_hash)
            await cache.set(
                key,
                {"score": score, "confidence": confidence, "label": label},
                expire=_NET_SCORE_CACHE_TTL,
            )
            await cache.close()
        except Exception:
            pass  # Non-fatal — DB result is authoritative

        return score, confidence, label
    except Exception as exc:
        logger.warning("DB network score lookup failed: %s", exc)
        return _BASELINE_SCORE, _BASELINE_CONFIDENCE, _BASELINE_LABEL


def extract_phone_hash_from_string(phone: str | None) -> str | None:
    """Normalize an Egyptian phone number to E.164 and HMAC-SHA256 it.

    Returns the 64-char hex digest, or ``None`` if the phone is missing,
    invalid, or the platform salt is unset.
    """
    if not phone:
        return None

    salt = get_settings().platform_secret_salt
    if not salt:
        logger.error(
            "PLATFORM_SECRET_SALT is not configured — cannot hash phone numbers"
        )
        return None

    return normalize_and_hash(phone, salt)


def extract_phone_hash_from_payload(payload: dict) -> str | None:
    """Extract a phone from a Shopify-style payload and hash it.

    Backwards-compatible helper kept for the Shopify webhook caller.
    Looks at customer.phone first, then shipping_address.phone.
    """
    customer = payload.get("customer") or {}
    shipping_address = payload.get("shipping_address") or {}
    phone = customer.get("phone") or shipping_address.get("phone") or ""
    return extract_phone_hash_from_string(phone)


async def write_network_event(
    *,
    phone_hash: str | None,
    store_id: UUID,
    event_type: Literal["order", "rto", "delivery", "refund"],
    network_repo: NetworkReputationRepository,
) -> None:
    """Write a network reputation event and refresh aggregates.

    Handles ``order``, ``rto``, ``delivery``, and ``refund``. Recomputes the
    cached score and invalidates the Redis cache for this phone hash. No-op
    if ``phone_hash`` is None.
    """
    if not phone_hash:
        return

    if event_type == "order":
        await network_repo.upsert_order(phone_hash=phone_hash, store_id=store_id)
    else:
        await network_repo.record_event(
            phone_hash=phone_hash,
            store_id=store_id,
            event_type=event_type,
        )

    # Update contributing store count and recompute cached score
    await network_repo.update_store_count(phone_hash)
    await network_repo.recompute_cached_score(phone_hash)

    # Invalidate Redis cache so the next lookup gets fresh data
    try:
        from src.infrastructure.cache.redis_cache import RedisCacheService

        cache = RedisCacheService()
        key = _NET_SCORE_CACHE_KEY.format(phone_hash=phone_hash)
        await cache.delete(key)
        await cache.close()
    except Exception:
        pass  # Non-fatal
