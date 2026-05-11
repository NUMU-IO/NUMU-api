"""MetaEventLog repository interface.

Phase 1 surfaces only the methods Phase 2's Celery task and the
dashboard endpoints will need. Intentionally narrower than the
generic CRUD ``BaseRepository`` because:

  * ``MetaEventLog`` is append-mostly — full mutation isn't needed.
  * The Celery task uses ``create()`` *as the dedup primitive* — it
    must surface ``IntegrityError`` so a concurrent retry can detect
    "already sent" without a separate SELECT.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.meta_event_log import MetaEventLog


class IMetaEventLogRepository(ABC):
    """Repository interface for ``meta_event_log`` rows."""

    @abstractmethod
    async def create(self, entity: MetaEventLog) -> MetaEventLog:
        """Insert a new event-log row.

        Raises:
            sqlalchemy.exc.IntegrityError: when a row already exists
                for ``(store_id, event_id)``. Phase 2's Celery task
                relies on this being raised (and not silently swallowed)
                as its "skip, already sent" signal.
        """
        ...

    @abstractmethod
    async def update_response(
        self,
        log_id: UUID,
        status: int,
        body: dict | None,
        fbtrace_id: str | None,
        sent_at: datetime,
    ) -> MetaEventLog | None:
        """Record Meta's HTTP response for a previously-inserted row.

        Returns the updated entity, or None if the row no longer exists
        (rare — implies admin deletion mid-flight).
        """
        ...

    @abstractmethod
    async def update_error(
        self,
        log_id: UUID,
        error: str,
        attempt_count: int,
    ) -> MetaEventLog | None:
        """Record a transient failure prior to a Celery retry.

        ``attempt_count`` is passed in (rather than incremented inside
        the repo) because the canonical retry counter lives on the
        Celery task — we mirror it on the row for dashboard display.
        """
        ...

    @abstractmethod
    async def recent_for_store(
        self,
        store_id: UUID,
        limit: int = 20,
    ) -> list[MetaEventLog]:
        """Newest-first slice for the merchant dashboard's "Recent events" table."""
        ...

    @abstractmethod
    async def count_failed_in_window(
        self,
        store_id: UUID,
        since: datetime,
    ) -> int:
        """Count rows for ``store_id`` with a 4xx/5xx response since ``since``.

        Drives the dashboard's connection-status badge (red when the
        last N events all failed) and the "10 consecutive 4xx" email
        alert outlined in plan §12.
        """
        ...
