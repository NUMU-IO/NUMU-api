"""Coverage / freshness report (FR-011, SC-002/SC-008).

Joins the area taxonomy with the `numu_knowledge_coverage` view so every area is
represented — including those with zero published articles (a gap). Applies the
staleness threshold from settings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.agent.knowledge.corpus_loader import load_areas
from src.config import settings as app_settings
from src.core.agent.knowledge import CoverageAreaRow, CoverageReport
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository


async def build_coverage_report(session: AsyncSession) -> CoverageReport:
    staleness_days = app_settings.agent_knowledge_staleness_days
    now = datetime.now(UTC)
    stale_before = now - timedelta(days=staleness_days)

    repo = KnowledgeRepository(session)
    rows_by_area = {r["area"]: r for r in await repo.coverage_rows()}

    areas: list[CoverageAreaRow] = []
    for area in load_areas():
        row = rows_by_area.get(area.key)
        published = int(row["published_count"]) if row else 0
        newest = row["newest_updated_at"] if row else None
        is_stale = bool(
            newest is not None and published > 0 and _aware(newest) < stale_before
        )
        areas.append(
            CoverageAreaRow(
                area=area.key,
                published_count=published,
                newest_updated_at=newest,
                is_gap=(published == 0),
                is_stale=is_stale,
            )
        )

    return CoverageReport(generated_at=now, staleness_days=staleness_days, areas=areas)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
