"""Moat-metrics endpoint — the due-diligence "does the network work?" view.

Platform-wide, internal-key protected, PII-free. Backs the acquisition data
room: coverage, cross-store catch, the auto-approve RTO delta, kill-switch
incidents, and the trust-tier distribution. See ``moat_metrics_service`` for
the math.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.shopify import verify_internal_key
from src.api.responses import SuccessResponse
from src.application.services.moat_metrics_service import gather_moat_metrics
from src.infrastructure.database.connection import get_admin_db_session

router = APIRouter(dependencies=[Depends(verify_internal_key)])


@router.get(
    "/moat-metrics",
    response_model=SuccessResponse[dict],
    summary="Platform-wide moat metrics (internal / due-diligence)",
    operation_id="get_moat_metrics",
)
async def get_moat_metrics(
    session: Annotated[AsyncSession, Depends(get_admin_db_session)],
):
    """Return the platform-wide proof-the-moat-works metrics."""
    metrics = await gather_moat_metrics(session)
    return SuccessResponse(data=metrics, message="Moat metrics")
