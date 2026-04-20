"""Admin review routes for marketplace theme moderation."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

router = APIRouter(prefix="/marketplace/admin", tags=["Marketplace Admin"])


@router.get("/pending")
async def list_pending_reviews():
    """List versions pending admin review."""
    pass


@router.post("/versions/{version_id}/review")
async def submit_review(version_id: UUID):
    """Submit a review decision (approve/reject) for a version."""
    pass
