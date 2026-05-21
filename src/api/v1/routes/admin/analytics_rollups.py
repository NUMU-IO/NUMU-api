"""Admin analytics-rollup management.

URL: /api/v1/admin/analytics-rollups
Requires SUPER_ADMIN role.

Used to recover specific gaps in analytics_daily_rollups without waiting
on the nightly Celery tick — e.g. after a beat outage long enough that
the default 90-day backfill window can't reach.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies.auth import require_admin
from src.api.responses import SuccessResponse
from src.infrastructure.messaging.tasks.analytics_rollup_tasks import (
    backfill_store_range,
)

router = APIRouter()


class BackfillRequest(BaseModel):
    store_id: UUID
    start_date: date = Field(description="Inclusive start of range to recompute (UTC).")
    end_date: date = Field(description="Inclusive end of range to recompute (UTC).")


class BackfillResponse(BaseModel):
    store_id: str
    days_written: int
    errors: int
    # Capped at the same limit the Celery task uses; logs have the full set.
    failures: list[tuple[str, str, str]]


@router.post(
    "/backfill",
    response_model=SuccessResponse[BackfillResponse],
    summary="Backfill analytics rollups for one store across a date range",
    operation_id="admin_backfill_analytics_rollups",
)
async def backfill_rollups(
    request: BackfillRequest,
    _admin: Annotated[object, Depends(require_admin)],
):
    """Recompute and upsert daily rollups for one store across an explicit
    date range. Each (store, day) runs in its own session so per-day
    failures don't poison the rest of the batch.

    Synchronous (not queued) so the caller sees the result directly.
    Cap your range — 365 days × 1 store is ~30 seconds of DB work.
    """
    span_days = (request.end_date - request.start_date).days + 1
    if span_days <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be on or after start_date",
        )
    if span_days > 365:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Range too wide ({span_days} days); max 365 per call",
        )

    try:
        result = await backfill_store_range(
            request.store_id, request.start_date, request.end_date
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return SuccessResponse(
        data=BackfillResponse(
            store_id=str(request.store_id),
            days_written=result["days_written"],
            errors=result["errors"],
            failures=result["failures"],
        ),
        message=(
            f"Backfilled {result['days_written']} of {span_days} day(s); "
            f"{result['errors']} error(s)"
        ),
    )
