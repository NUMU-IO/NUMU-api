"""Risk routes — narrative generation, moat metrics + (future) batch lookups."""

from fastapi import APIRouter

from src.api.v1.routes.risk.moat_metrics import router as moat_metrics_router
from src.api.v1.routes.risk.narrative import router as narrative_router

router = APIRouter()
router.include_router(narrative_router, tags=["Risk - Narrative"])
router.include_router(moat_metrics_router, tags=["Risk - Moat Metrics"])

__all__ = ["router"]
