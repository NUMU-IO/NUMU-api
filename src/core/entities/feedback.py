"""Beta merchant feedback entity."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class FeedbackCategory(StrEnum):
    """Feedback category for beta merchant reports."""

    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    USABILITY = "usability"
    PERFORMANCE = "performance"
    PAYMENT = "payment"
    GENERAL = "general"


class Feedback(BaseEntity):
    """Beta merchant feedback entry."""

    store_id: UUID
    user_id: UUID
    category: FeedbackCategory
    rating: int = Field(ge=1, le=5)
    title: str
    body: str
    contact_ok: bool = True
