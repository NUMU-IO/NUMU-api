"""Store feedback routes.

URL: /stores/{store_id}/feedback
Allows beta merchants to submit product feedback.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_store_owner
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.api.v1.schemas.tenant.feedback import (
    CreateFeedbackRequest,
    FeedbackResponse,
)
from src.core.entities.feedback import Feedback, FeedbackCategory
from src.infrastructure.repositories.feedback_repository import FeedbackRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/feedback")


def _build_response(fb: Feedback) -> FeedbackResponse:
    return FeedbackResponse(
        id=fb.id,
        store_id=fb.store_id,
        user_id=fb.user_id,
        category=fb.category,
        rating=fb.rating,
        title=fb.title,
        body=fb.body,
        contact_ok=fb.contact_ok,
        created_at=fb.created_at,
    )


@router.post(
    "/",
    response_model=SuccessResponse[FeedbackResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Submit beta feedback",
    operation_id="create_feedback",
)
async def create_feedback(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CreateFeedbackRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Submit feedback for the NUMU platform.

    Available to store owners during the beta period.
    """
    try:
        category = FeedbackCategory(request.category)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category. Must be one of: {', '.join(c.value for c in FeedbackCategory)}",
        )

    entry = Feedback(
        store_id=store_id,
        user_id=user_id,
        category=category,
        rating=request.rating,
        title=request.title,
        body=request.body,
        contact_ok=request.contact_ok,
    )

    repo = FeedbackRepository(db)
    created = await repo.create(entry)
    await db.commit()

    logger.info(
        "feedback_submitted",
        extra={
            "store_id": str(store_id),
            "category": request.category,
            "rating": request.rating,
        },
    )

    return SuccessResponse(
        data=_build_response(created),
        message="Feedback submitted — thank you!",
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[FeedbackResponse]],
    summary="List store feedback",
    operation_id="list_store_feedback",
)
async def list_store_feedback(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List feedback submitted by this store."""
    repo = FeedbackRepository(db)

    skip = (page - 1) * page_size
    items = await repo.list_all(store_id=store_id, skip=skip, limit=page_size)
    total = await repo.count(store_id=store_id)

    return SuccessResponse(
        data=PaginatedListResponse(
            items=[_build_response(fb) for fb in items],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
        ),
    )
