"""DTOs for promotion analytics events."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums.promotion_enums import PromotionEventType


class RecordPromotionEventInput(BaseModel):
    """Payload for `POST /storefront/.../promotions/events`."""

    model_config = ConfigDict(extra="forbid")

    promotion_id: UUID
    event_type: PromotionEventType
    customer_id: UUID | None = None
    session_id: str | None = Field(default=None, max_length=64)
    order_id: UUID | None = None
    discount_amount_cents: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DismissPromotionInput(BaseModel):
    """Payload for `POST /storefront/.../promotions/dismiss`."""

    model_config = ConfigDict(extra="forbid")

    promotion_id: UUID
    customer_id: UUID | None = None
    visitor_token: str | None = Field(default=None, max_length=64)


class DailyAnalytics(BaseModel):
    """Per-day aggregate row inside the analytics response."""

    model_config = ConfigDict(extra="forbid")

    day: date
    impressions: int = 0
    clicks: int = 0
    dismissals: int = 0
    redemptions: int = 0
    conversions: int = 0
    revenue_cents: int = 0


class PromotionAnalyticsOutput(BaseModel):
    """Returned by `GET /stores/{id}/promotions/{pid}/analytics`."""

    model_config = ConfigDict(extra="forbid")

    promotion_id: UUID
    range_start: date
    range_end: date
    impressions: int = 0
    clicks: int = 0
    dismissals: int = 0
    redemptions: int = 0
    conversions: int = 0
    revenue_cents: int = 0
    discount_total_cents: int = 0
    by_day: list[DailyAnalytics] = Field(default_factory=list)
    conversion_rate: float = 0.0
    impression_to_click_rate: float = 0.0
    generated_at: datetime
