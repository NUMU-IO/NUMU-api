"""Marketplace theme review routes.

Auth model:
  * `GET /marketplace/themes/{theme_id}/reviews` is unauthenticated —
    anyone browsing the catalog can read reviews.
  * `POST /marketplace/themes/{theme_id}/reviews`,
    `PUT /marketplace/reviews/{id}`,
    `DELETE /marketplace/reviews/{id}` — JWT auth; user_id off the JWT
    is the author, the service enforces "only the author can edit".
  * `POST /marketplace/reviews/{id}/respond` — JWT auth; the service
    confirms the responder is the theme's developer_id.

Error semantics:
  * 400 for input errors (bad rating, already reviewed, not verified).
  * 404 for missing review/theme to avoid leaking ownership info.

Aggregate update:
  * Every mutation eventually calls
    `MarketplaceRepository.recompute_theme_rating_aggregates` in the
    same DB transaction so `marketplace_themes.{average_rating, review_count}`
    are always consistent with the rows in `marketplace_theme_reviews`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies import get_current_user_id
from src.api.dependencies.repositories import get_marketplace_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    CreateReviewRequest,
    DeveloperResponseRequest,
    ReviewListResponse,
    ReviewOut,
    UpdateReviewRequest,
)
from src.application.services.theme_review_service import ThemeReviewService
from src.core.entities.marketplace_theme import MarketplaceThemeReview
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

router = APIRouter(prefix="/marketplace", tags=["Marketplace Reviews"])


def _svc(
    repo: Annotated[MarketplaceRepository, Depends(get_marketplace_repository)],
) -> ThemeReviewService:
    return ThemeReviewService(marketplace_repo=repo)


def _to_out(r: MarketplaceThemeReview) -> ReviewOut:
    return ReviewOut(
        id=str(r.id),
        marketplace_theme_id=str(r.marketplace_theme_id),
        user_id=str(r.user_id),
        rating=r.rating,
        title=r.title,
        body=r.body,
        is_verified_purchase=r.is_verified_purchase,
        developer_response=r.developer_response,
        developer_response_at=r.developer_response_at.isoformat()
        if r.developer_response_at
        else None,
        helpful_count=r.helpful_count,
        created_at=r.created_at.isoformat() if r.created_at else None,
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
    )


# ── Public read ──────────────────────────────────────────────────────────────


@router.get(
    "/themes/{theme_id}/reviews",
    response_model=SuccessResponse[ReviewListResponse],
)
async def list_reviews(
    theme_id: UUID,
    svc: Annotated[ThemeReviewService, Depends(_svc)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List reviews for a theme (public — no auth)."""
    data = await svc.list_for_theme(theme_id, page=page, per_page=per_page)
    return SuccessResponse(
        data=ReviewListResponse(
            reviews=[_to_out(r) for r in data["reviews"]],
            total=data["total"],
            page=data["page"],
            per_page=data["per_page"],
        )
    )


# ── Authenticated write ──────────────────────────────────────────────────────


@router.post(
    "/themes/{theme_id}/reviews",
    response_model=SuccessResponse[ReviewOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_review(
    theme_id: UUID,
    body: CreateReviewRequest,
    svc: Annotated[ThemeReviewService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Create a review for a theme.

    Refused if the user already reviewed it (PUT instead) or hasn't
    purchased/installed it (only verified buyers can review).
    """
    try:
        r = await svc.create_review(
            marketplace_theme_id=theme_id,
            user_id=user_id,
            rating=body.rating,
            title=body.title,
            body=body.body,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=_to_out(r))


@router.put(
    "/reviews/{review_id}",
    response_model=SuccessResponse[ReviewOut],
)
async def update_review(
    review_id: UUID,
    body: UpdateReviewRequest,
    svc: Annotated[ThemeReviewService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Edit your own review. Returns 404 if the review doesn't exist
    or belongs to another user."""
    try:
        r = await svc.update_review(
            review_id=review_id,
            user_id=user_id,
            rating=body.rating,
            title=body.title,
            body=body.body,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=_to_out(r))


@router.delete("/reviews/{review_id}")
async def delete_review(
    review_id: UUID,
    svc: Annotated[ThemeReviewService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Delete your own review. Idempotent — returns 200 either way."""
    ok = await svc.delete_review(review_id=review_id, user_id=user_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review not found"
        )
    return SuccessResponse(
        data={"deleted": True, "review_id": str(review_id)},
        message="Review deleted",
    )


@router.post(
    "/reviews/{review_id}/respond",
    response_model=SuccessResponse[ReviewOut],
)
async def respond_to_review(
    review_id: UUID,
    body: DeveloperResponseRequest,
    svc: Annotated[ThemeReviewService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Theme developer posts a single response to a review.

    The service confirms `user_id == theme.developer_id`. Subsequent
    calls overwrite the prior response (single-thread, matches Shopify).
    """
    try:
        r = await svc.respond_to_review(
            review_id=review_id,
            developer_user_id=user_id,
            response_text=body.response,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=_to_out(r))
