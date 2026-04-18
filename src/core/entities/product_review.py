"""Product review entity."""

from uuid import UUID

from pydantic import Field, field_validator

from src.core.entities.base import BaseEntity


class ProductReview(BaseEntity):
    """A customer review of a product.

    Reviews are tenant-scoped (each store has its own reviews) and tied to
    both a product and the customer who posted them. An approval gate allows
    stores to moderate reviews before they appear publicly.
    """

    tenant_id: UUID
    store_id: UUID
    product_id: UUID
    customer_id: UUID | None = None  # null when review is from a legacy/guest flow
    reviewer_name: str = Field(..., min_length=1, max_length=120)
    rating: int = Field(..., ge=1, le=5)
    title: str | None = Field(None, max_length=200)
    body: str | None = Field(None, max_length=4000)
    is_approved: bool = True  # auto-approved by default; store can disable later
    helpful_count: int = 0

    @field_validator("reviewer_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("reviewer_name cannot be blank")
        return v

    @field_validator("title", "body")
    @classmethod
    def _strip_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        return v or None
