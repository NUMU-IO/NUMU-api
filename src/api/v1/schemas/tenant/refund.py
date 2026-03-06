"""Refund Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.api.dependencies.sanitization import SanitizedStr


class CreateRefundRequest(BaseModel):
    """Create refund request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "refund_type": "full",
                "reason": "customer_request",
                "reason_note": "Customer changed their mind",
            }
        }
    )

    refund_type: str = Field(
        ...,
        description="Refund type: 'full' or 'partial'",
        pattern="^(full|partial)$",
    )
    reason: str = Field(
        ...,
        description="Refund reason: defective, wrong_item, not_as_described, customer_request, duplicate_order, other",
    )
    reason_note: SanitizedStr | None = Field(
        None,
        max_length=1000,
        description="Additional details about the refund reason",
    )
    amount: int | None = Field(
        None,
        ge=1,
        description="Refund amount in cents (required for partial refunds)",
    )


class RejectRefundRequest(BaseModel):
    """Reject refund request schema."""

    reason: SanitizedStr | None = Field(
        None,
        max_length=500,
        description="Reason for rejecting the refund",
    )


class RefundResponse(BaseModel):
    """Refund response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    store_id: str
    refund_number: str
    refund_type: str
    status: str
    reason: str
    reason_note: str | None
    amount: int
    currency: str
    payment_provider: str | None
    payment_id: str | None
    provider_refund_id: str | None
    requested_by: str | None
    approved_by: str | None
    rejected_by: str | None
    processed_at: datetime | None
    completed_at: datetime | None
    rejected_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class RefundListItemResponse(BaseModel):
    """Refund list item response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    refund_number: str
    order_id: str
    order_number: str | None
    refund_type: str
    status: str
    reason: str
    amount: int
    currency: str
    created_at: datetime
