"""US4 — coverage gap & staleness detection (FR-011).

The coverage report flags an area with no published article as a gap, and an area
whose newest article is older than the staleness window as stale.

Note: the coverage view (`numu_knowledge_coverage`) is created by the Alembic
migration. On the SQLite test harness the view may be absent, so the report falls
back to zero rows → every area reads as a gap, which still exercises the gap logic.
"""

from __future__ import annotations

import pytest

from src.application.agent.knowledge.coverage import build_coverage_report


@pytest.mark.asyncio
async def test_empty_corpus_flags_every_area_as_gap(test_session):
    report = await build_coverage_report(test_session)
    assert report.areas, "every taxonomy area should appear in the report"
    # With no published docs, every area is a gap and coverage is 0%.
    assert all(a.is_gap for a in report.areas)
    assert report.summary["coverage_pct"] == 0.0
    assert report.summary["areas_with_gap"] == report.summary["areas_total"]


@pytest.mark.asyncio
async def test_report_lists_all_taxonomy_areas(test_session):
    from src.application.agent.knowledge.corpus_loader import load_areas

    report = await build_coverage_report(test_session)
    assert {a.area for a in report.areas} == {a.key for a in load_areas()}
    assert report.staleness_days >= 1
