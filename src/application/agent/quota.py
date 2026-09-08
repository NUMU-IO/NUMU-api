"""Per-store daily turn cap.

The agent is billed per token and re-sends its context on every iteration, so
the cost of a store is bounded only by how much its staff type. That is fine
until it isn't: a scripted client, a retry loop in the panel, or one very
enthusiastic merchant is a bill rather than merely load, and nothing else in
the path counts turns.

Counting, not throttling. The cap is a ceiling that should never be reached in
normal use; a merchant who hits it has done something unusual and an operator
should see it. Per store rather than per staff member, because the bill is per
store.

Fails **open**. Redis is a cache here, not a ledger: if it is unavailable the
merchant keeps their assistant and we lose a day's count, which is the better
of the two failures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from src.core.logging import get_logger

logger = get_logger(__name__)

_DAY = 24 * 60 * 60


def _key(store_id: UUID) -> str:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"agent:turns:{store_id}:{day}"


async def consume_turn(cache, store_id: UUID, *, limit: int) -> bool:
    """Count one turn. False when the store is over its daily cap.

    ``limit <= 0`` disables the cap entirely.
    """
    if limit <= 0:
        return True

    key = _key(store_id)
    try:
        used = await cache.increment(key)
        # increment() returns 0 when Redis is unreachable — that is the
        # fail-open path, not a store that has used zero turns.
        if used == 0:
            return True
        if used == 1:
            # First turn of the day: give the counter a TTL so the key expires
            # on its own rather than accumulating one row per store per day.
            await cache.set(key, used, expire=_DAY)
        if used > limit:
            logger.warning(
                "agent_turn_quota_exceeded",
                store_id=str(store_id),
                used=used,
                limit=limit,
            )
            return False
    except Exception:  # noqa: BLE001 — a cache fault must not close the agent
        logger.warning("agent_turn_quota_check_failed", store_id=str(store_id))
        return True
    return True


def quota_message(locale: str) -> str:
    if locale == "ar":
        return "خلصت رسائل المساعد النهاردة. جرّب تاني بكرة."
    return "You've reached today's assistant limit. It resets tomorrow."
