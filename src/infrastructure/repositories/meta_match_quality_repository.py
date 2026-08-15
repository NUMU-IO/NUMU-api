"""Repository for ``meta_match_quality_snapshot`` — EMQ history.

Mirrors the conventions of the sibling tenant-scoped repositories: an
explicit ``tenant_id`` filter alongside Postgres RLS, and no business logic
beyond shaping rows into the service's dataclass.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.meta_match_quality_service import MatchQualitySnapshot
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.meta_match_quality_snapshot import (
    MetaMatchQualitySnapshotModel,
)


class MetaMatchQualityRepository:
    """Async SQLAlchemy repository for EMQ snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _scoped(self, stmt):
        tenant_id = get_tenant_id()
        if tenant_id:
            stmt = stmt.where(MetaMatchQualitySnapshotModel.tenant_id == tenant_id)
        return stmt

    async def record(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        snapshots: list[MatchQualitySnapshot],
    ) -> int:
        """Append one row per snapshot. Returns the number written."""
        if not snapshots:
            return 0
        self.session.add_all([
            MetaMatchQualitySnapshotModel(
                tenant_id=tenant_id,
                store_id=store_id,
                pixel_id=snap.pixel_id,
                event_name=snap.event_name,
                emq_score=snap.emq_score,
                dedup_rate=snap.dedup_rate,
                event_coverage=snap.event_coverage,
                total_events=snap.total_events,
                match_key_coverage=snap.match_key_coverage or {},
                diagnostics=snap.diagnostics or [],
                data_freshness=snap.data_freshness,
                captured_at=snap.captured_at,
            )
            for snap in snapshots
        ])
        await self.session.flush()
        return len(snapshots)

    async def latest_per_event(
        self, store_id: UUID, pixel_id: str | None = None
    ) -> list[MatchQualitySnapshot]:
        """Newest snapshot for each event name, for the dashboard.

        Deliberately a plain ordered scan + first-wins dedupe in Python rather
        than a window function: a store has a handful of event names and at
        most a few hundred rows in the retention window, and the covering
        index makes this an index scan. A ``DISTINCT ON`` would be marginally
        tighter and considerably harder to read.
        """
        stmt = select(MetaMatchQualitySnapshotModel).where(
            MetaMatchQualitySnapshotModel.store_id == store_id
        )
        if pixel_id:
            stmt = stmt.where(MetaMatchQualitySnapshotModel.pixel_id == pixel_id)
        stmt = self._scoped(stmt).order_by(
            MetaMatchQualitySnapshotModel.captured_at.desc()
        )

        rows = (await self.session.execute(stmt)).scalars().all()
        seen: set[tuple[str, str]] = set()
        out: list[MatchQualitySnapshot] = []
        for row in rows:
            key = (row.pixel_id, row.event_name)
            if key in seen:
                continue
            seen.add(key)
            out.append(_to_dataclass(row))
        return out

    async def history_for_event(
        self, store_id: UUID, pixel_id: str, event_name: str, limit: int = 30
    ) -> list[MatchQualitySnapshot]:
        """Oldest→newest series for one event — the "did it improve?" chart."""
        stmt = (
            select(MetaMatchQualitySnapshotModel)
            .where(
                MetaMatchQualitySnapshotModel.store_id == store_id,
                MetaMatchQualitySnapshotModel.pixel_id == pixel_id,
                MetaMatchQualitySnapshotModel.event_name == event_name,
            )
            .order_by(MetaMatchQualitySnapshotModel.captured_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(self._scoped(stmt))).scalars().all()
        return [_to_dataclass(r) for r in reversed(rows)]

    async def prune_older_than(self, days: int = 180) -> int:
        """Drop snapshots past the retention window.

        `meta_event_log` has no retention policy and grows forever; this table
        polls on a schedule, so without pruning it would grow faster. 180 days
        keeps a season-over-season comparison and bounds the table.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await self.session.execute(
            delete(MetaMatchQualitySnapshotModel).where(
                MetaMatchQualitySnapshotModel.captured_at < cutoff
            )
        )
        return int(result.rowcount or 0)


def _to_dataclass(row: MetaMatchQualitySnapshotModel) -> MatchQualitySnapshot:
    return MatchQualitySnapshot(
        pixel_id=row.pixel_id,
        event_name=row.event_name,
        emq_score=float(row.emq_score),
        dedup_rate=float(row.dedup_rate) if row.dedup_rate is not None else 0.0,
        total_events=int(row.total_events or 0),
        captured_at=row.captured_at,
        match_key_coverage=dict(row.match_key_coverage or {}),
        diagnostics=list(row.diagnostics or []),
        event_coverage=(
            float(row.event_coverage) if row.event_coverage is not None else None
        ),
        data_freshness=row.data_freshness,
    )
