"""Admin feedback aggregation endpoint.

URL: /api/v1/admin/feedback
Requires SUPER_ADMIN role.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.feedback import FeedbackResponse, FeedbackSummaryResponse
from src.core.entities.feedback import FeedbackCategory
from src.infrastructure.repositories.feedback_repository import FeedbackRepository

router = APIRouter()


@router.get(
    "/",
    response_model=SuccessResponse[FeedbackSummaryResponse],
    summary="Get aggregated feedback",
)
async def get_feedback_summary(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    category: Annotated[FeedbackCategory | None, Query()] = None,
):
    """Get aggregated feedback across all stores.

    Returns total count, average rating, category breakdown, and
    the 20 most recent entries.
    """
    repo = FeedbackRepository(db)

    total = await repo.count(category=category)
    avg_rating = await repo.average_rating()
    breakdown = await repo.category_breakdown()
    recent = await repo.list_all(category=category, limit=20)

    return SuccessResponse(
        data=FeedbackSummaryResponse(
            total=total,
            average_rating=avg_rating,
            category_breakdown=breakdown,
            recent=[
                FeedbackResponse(
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
                for fb in recent
            ],
        ),
    )
