"""Theme error telemetry — merchant-facing read routes.

URL: ``/stores/{store_id}/theme-errors``

Surfaces the durable shopper-side bundle-crash telemetry (written by the
public ``/storefront/store/{store_id}/theme-error`` beacon) as a per-version
crash summary, so a merchant or platform operator can spot "version X
spiked right after we published it" (Phase 3 moat item).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_theme_error_event_repository
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.repositories.theme_error_event_repository import (
    ThemeErrorEventRepository,
)

router = APIRouter(prefix="/{store_id}/theme-errors")


class ThemeVersionErrorStat(BaseModel):
    """Crash aggregate for a single ``theme_version`` over the window."""

    theme_version: str | None
    theme_slug: str | None
    error_count: int
    last_seen: datetime


class ThemeErrorSummaryResponse(BaseModel):
    """Per-version crash summary over a trailing window."""

    window_days: int
    since: datetime
    total_errors: int
    versions: list[ThemeVersionErrorStat]


@router.get(
    "/summary",
    response_model=SuccessResponse[ThemeErrorSummaryResponse],
    summary="Per-version theme crash summary",
    operation_id="get_theme_error_summary",
)
async def get_theme_error_summary(
    store: Annotated[Store, Depends(verify_store_ownership)],
    theme_error_repo: Annotated[
        ThemeErrorEventRepository, Depends(get_theme_error_event_repository)
    ],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> SuccessResponse[ThemeErrorSummaryResponse]:
    """Crash counts + last_seen per ``theme_version`` over the last ``days``.

    Store-owner gated (``verify_store_ownership``); the underlying query is
    store_id + tenant scoped and served by the
    ``(store_id, theme_version, occurred_at)`` index.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await theme_error_repo.get_version_summary(store_id=store.id, since=since)

    versions = [
        ThemeVersionErrorStat(
            theme_version=row.theme_version,
            theme_slug=row.theme_slug,
            error_count=row.error_count,
            last_seen=row.last_seen,
        )
        for row in rows
    ]
    return SuccessResponse(
        data=ThemeErrorSummaryResponse(
            window_days=days,
            since=since,
            total_errors=sum(v.error_count for v in versions),
            versions=versions,
        )
    )
