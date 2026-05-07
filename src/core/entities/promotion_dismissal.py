"""PromotionDismissal entity — per-customer / per-visitor suppression."""

from datetime import UTC, datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PromotionDismissal(BaseModel):
    """One shopper dismissed one promotion.

    Exactly one of `customer_id` or `visitor_token` must be set —
    enforced both here and via DB CHECK constraint.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True, populate_by_name=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    promotion_id: UUID
    customer_id: UUID | None = None
    visitor_token: str | None = None
    dismissed_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> Self:
        has_customer = self.customer_id is not None
        has_visitor = self.visitor_token is not None
        if has_customer == has_visitor:
            raise ValueError("exactly one of customer_id or visitor_token must be set")
        return self
