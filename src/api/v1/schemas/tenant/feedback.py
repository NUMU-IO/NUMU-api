"""Feedback request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.api.dependencies.sanitization import SanitizedStr


class CreateFeedbackRequest(BaseModel):
    """Create feedback request."""

    category: str = Field(
        ...,
        description="bug | feature_request | usability | performance | payment | general",
    )
    rating: int = Field(..., ge=1, le=5)
    title: SanitizedStr = Field(..., min_length=3, max_length=255)
    body: SanitizedStr = Field(..., min_length=10, max_length=5000)
    contact_ok: bool = True


class FeedbackResponse(BaseModel):
    """Feedback response."""

    id: UUID
    store_id: UUID
    user_id: UUID
    category: str
    rating: int
    title: str
    body: str
    contact_ok: bool
    created_at: datetime

    class Config:
        from_attributes = True


class FeedbackSummaryResponse(BaseModel):
    """Admin feedback aggregation response."""

    total: int
    average_rating: float
    category_breakdown: dict[str, int]
    recent: list[FeedbackResponse]
