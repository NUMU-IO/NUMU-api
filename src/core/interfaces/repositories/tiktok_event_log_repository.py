"""TikTokEventLog repository interface.

Sibling of ``IMetaEventLogRepository``. Surfaces only the methods the
Celery task and the merchant-hub settings endpoints need. Intentionally
narrower than the generic CRUD ``BaseRepository`` because:

  * ``TikTokEventLog`` is append-mostly — full mutation isn't needed.
  * The Celery task uses ``create()`` *as the dedup primitive* — it must
    surface ``IntegrityError`` so a concurrent retry can detect "already
    sent" without a separate SELECT.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.tiktok_event_log import TikTokEventLog


class ITikTokEventLogRepository(ABC):
    """Repository interface for ``tiktok_event_log`` rows."""

    @abstractmethod
    async def create(self, entity: TikTokEventLog) -> TikTokEventLog:
        """Insert a new event-log row.

        Raises:
            sqlalchemy.exc.IntegrityError: when a row already exists for
                ``(store_id, event_id)``. The Celery task relies on this
                being raised (not silently swallowed) as its "skip,
                already sent" signal.
        """
        ...

    @abstractmethod
    async def update_response(
        self,
        log_id: UUID,
        status: int,
        code: int | None,
        body: dict | None,
        request_id: str | None,
        sent_at: datetime,
        attempt_count: int | None = None,
    ) -> TikTokEventLog | None:
        """Record TikTok's HTTP response (+ business code) for a row.

        ``attempt_count`` is written only when supplied, so a retry can bump
        it without every first-attempt caller having to pass 1.

        Returns the updated entity, or None if the row no longer exists.
        """
        ...

    @abstractmethod
    async def update_error(
        self,
        log_id: UUID,
        error: str,
        attempt_count: int,
    ) -> TikTokEventLog | None:
        """Record a transient failure prior to a Celery retry."""
        ...

    @abstractmethod
    async def get_for_event(
        self,
        store_id: UUID,
        pixel_id: str,
        event_id: str,
    ) -> TikTokEventLog | None:
        """Fetch the row a UNIQUE violation collided with, if it exists.

        Used by the Celery task to adopt its own row on a retry instead of
        mistaking it for another producer's event.
        """
        ...

    @abstractmethod
    async def recent_for_store(
        self,
        store_id: UUID,
        limit: int = 20,
    ) -> list[TikTokEventLog]:
        """Newest-first slice for the merchant dashboard's "Recent events" table."""
        ...

    @abstractmethod
    async def failed_for_store(
        self,
        store_id: UUID,
        since: datetime,
        limit: int = 50,
    ) -> list[TikTokEventLog]:
        """Newest-first slice of rows TikTok never accepted.

        "Failed" matches the partial index `idx_tiktok_event_log_failed`:
        no response recorded, an HTTP 4xx/5xx, or a non-zero business code.
        Drives the dead-letter view and the replay action.
        """
        ...

    @abstractmethod
    async def count_failed_in_window(
        self,
        store_id: UUID,
        since: datetime,
    ) -> int:
        """Count rows for ``store_id`` that failed since ``since``.

        "Failed" = HTTP 4xx/5xx, no response recorded, OR a non-zero
        business ``response_code``. Drives the dashboard connection-status
        badge.
        """
        ...
