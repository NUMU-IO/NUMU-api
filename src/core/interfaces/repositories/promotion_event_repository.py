"""PromotionEvent repository protocol — append-only writes + aggregations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.core.entities.promotion_event import PromotionEvent
from src.core.enums.promotion_enums import PromotionEventType


@dataclass(frozen=True)
class PromotionEventCounts:
    """Aggregate metrics for one promotion over a date range."""

    promotion_id: UUID
    impressions: int = 0
    clicks: int = 0
    dismissals: int = 0
    redemptions: int = 0
    conversions: int = 0
    revenue_cents: int = 0


class IPromotionEventRepository(ABC):
    """Append-only persistence + read-side aggregations."""

    @abstractmethod
    async def record(self, event: PromotionEvent) -> None: ...

    @abstractmethod
    async def record_many(self, events: list[PromotionEvent]) -> None: ...

    @abstractmethod
    async def counts_for_promotion(
        self,
        promotion_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> PromotionEventCounts: ...

    @abstractmethod
    async def counts_for_store(
        self,
        store_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        event_types: list[PromotionEventType] | None = None,
    ) -> dict[UUID, PromotionEventCounts]:
        """Map of promotion_id -> aggregate counts within the window."""
