"""Get daily order-streak use case.

Computes how many consecutive days (in the store's local timezone) the store
has had at least one real order, for the celebratory streak badge on the
merchant dashboard. A streak is "alive" as long as there was an order today
*or* yesterday — the merchant still has all of today to keep yesterday's run
going, so we don't reset the moment midnight passes.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository

# Egypt observes no DST, so Africa/Cairo is a stable UTC+2. Matches the
# placeholder default used elsewhere in the codebase until a per-store
# timezone resolver lands.
STORE_TIMEZONE = "Africa/Cairo"

# How far back to scan when computing the streak. A year comfortably covers
# any realistic active streak while keeping the query bounded.
_LOOKBACK_DAYS = 366


@dataclass
class OrderStreakDTO:
    """Daily order-streak summary."""

    current_streak: int  # consecutive days ending today or yesterday
    longest_streak: int  # best run within the lookback window
    last_order_date: date | None
    active_today: bool  # an order already landed today (streak locked in)


class GetOrderStreakUseCase:
    """Compute the store's consecutive-days-with-orders streak."""

    def __init__(
        self,
        *,
        order_repository: IOrderRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.order_repository = order_repository
        self.store_repository = store_repository

    async def execute(self, *, store_id: UUID, user_id: UUID) -> OrderStreakDTO:
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to view this store")

        tz = ZoneInfo(STORE_TIMEZONE)
        today = datetime.now(tz).date()
        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(days=_LOOKBACK_DAYS)

        days = await self.order_repository.get_order_day_set(
            store_id, window_start, window_end, timezone=STORE_TIMEZONE
        )

        return OrderStreakDTO(
            current_streak=_current_streak(days, today),
            longest_streak=_longest_streak(days),
            last_order_date=max(days) if days else None,
            active_today=today in days,
        )


def _current_streak(days: set[date], today: date) -> int:
    """Count back from today (or yesterday, if today has no order yet) while
    each preceding day also has an order."""
    yesterday = today - timedelta(days=1)
    if today in days:
        cursor = today
    elif yesterday in days:
        cursor = yesterday
    else:
        return 0

    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _longest_streak(days: set[date]) -> int:
    """Longest consecutive run anywhere in the lookback window."""
    if not days:
        return 0
    best = 0
    for day in days:
        # Only begin counting at the first day of a run.
        if (day - timedelta(days=1)) in days:
            continue
        run = 0
        cursor = day
        while cursor in days:
            run += 1
            cursor += timedelta(days=1)
        best = max(best, run)
    return best
