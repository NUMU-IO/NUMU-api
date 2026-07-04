"""Theme error event repository — durable theme bundle-crash telemetry.

Backs the shopper-side beacon ingest (append-only ``create``) and the
merchant-facing per-version crash summary read. Read queries carry an
explicit ``tenant_id`` filter as defense-in-depth alongside the ``store_id``
filter and PostgreSQL RLS.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.theme_error_event import (
    ThemeErrorEventModel,
)


@dataclass(slots=True)
class ThemeVersionErrorSummary:
    """Per-``theme_version`` crash aggregate over a window."""

    theme_version: str | None
    theme_slug: str | None
    error_count: int
    last_seen: datetime


class ThemeErrorEventRepository:
    """Repository for ``theme_error_events`` (append-only)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query: Select[Any]) -> Select[Any]:
        """Apply tenant_id filter when a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(ThemeErrorEventModel.tenant_id == tid)
        return query

    async def create(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        message: str,
        theme_slug: str | None = None,
        theme_version: str | None = None,
        bundle_url: str | None = None,
        url: str | None = None,
    ) -> None:
        """Insert one theme error event.

        ``occurred_at`` / ``created_at`` fall back to the DB ``now()``
        server defaults.
        """
        event = ThemeErrorEventModel(
            store_id=store_id,
            tenant_id=tenant_id,
            message=message,
            theme_slug=theme_slug,
            theme_version=theme_version,
            bundle_url=bundle_url,
            url=url,
        )
        self.session.add(event)
        await self.session.flush()

    async def get_version_summary(
        self,
        *,
        store_id: UUID,
        since: datetime,
    ) -> list[ThemeVersionErrorSummary]:
        """Per-``theme_version`` crash counts + last_seen since ``since``.

        Uses the ``(store_id, theme_version, occurred_at)`` index. Ordered by
        count descending so a spiking version surfaces first.
        """
        query = (
            select(
                ThemeErrorEventModel.theme_version,
                func.max(ThemeErrorEventModel.theme_slug).label("theme_slug"),
                func.count().label("error_count"),
                func.max(ThemeErrorEventModel.occurred_at).label("last_seen"),
            )
            .where(ThemeErrorEventModel.store_id == store_id)
            .where(ThemeErrorEventModel.occurred_at >= since)
            .group_by(ThemeErrorEventModel.theme_version)
            .order_by(func.count().desc())
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return [
            ThemeVersionErrorSummary(
                theme_version=row.theme_version,
                theme_slug=row.theme_slug,
                error_count=row.error_count,
                last_seen=row.last_seen,
            )
            for row in result.all()
        ]
