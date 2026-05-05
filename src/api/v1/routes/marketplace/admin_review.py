"""Admin review routes for marketplace theme moderation.

All routes require SUPER_ADMIN — gated by the same `require_admin`
dependency used by the rest of the admin API. Reads the admin cookie
namespace so impersonation can't evict the admin session.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies.auth import require_admin
from src.api.dependencies.repositories import get_marketplace_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    PendingReviewListResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
)
from src.application.services.marketplace_service import MarketplaceService
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

router = APIRouter(
    prefix="/marketplace/admin",
    tags=["Marketplace Admin"],
    dependencies=[Depends(require_admin)],
)


def _svc(
    repo: Annotated[MarketplaceRepository, Depends(get_marketplace_repository)],
) -> MarketplaceService:
    return MarketplaceService(marketplace_repo=repo)


@router.get("/pending", response_model=SuccessResponse[PendingReviewListResponse])
async def list_pending_reviews(
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """List versions awaiting admin review."""
    pending = await svc.list_pending_reviews()
    return SuccessResponse(data=PendingReviewListResponse(pending=pending))


@router.post(
    "/versions/{version_id}/review",
    response_model=SuccessResponse[ReviewDecisionResponse],
)
async def submit_review(
    version_id: UUID,
    body: ReviewDecisionRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Approve or reject a version. Approval publishes the listing."""
    try:
        data = await svc.review_version(
            reviewer_id=admin_id,
            version_id=version_id,
            decision=body.decision,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=ReviewDecisionResponse(**data))
