"""Risk routes — narrative generation + (future) batch lookups."""

from fastapi import APIRouter

from src.api.v1.routes.risk.narrative import router as narrative_router

router = APIRouter()
router.include_router(narrative_router, tags=["Risk - Narrative"])

__all__ = ["router"]
