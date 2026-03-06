"""Refund DTOs."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.refund import Refund


@dataclass
class CreateRefundDTO(BaseDTO):
    """Create refund data transfer object."""

    order_id: UUID
    refund_type: str  # "full" | "partial"
    reason: str  # RefundReason value
    reason_note: str | None = None
    amount: int | None = None  # required for partial, auto-calculated for full


@dataclass
class RefundDTO(BaseDTO):
    """Refund data transfer object."""

    id: UUID
    order_id: UUID
    store_id: UUID
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
    requested_by: UUID | None
    approved_by: UUID | None
    rejected_by: UUID | None
    processed_at: datetime | None
    completed_at: datetime | None
    rejected_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: Refund) -> "RefundDTO":
        """Create DTO from Refund entity."""
        return cls(
            id=entity.id,
            order_id=entity.order_id,
            store_id=entity.store_id,
            refund_number=entity.refund_number,
            refund_type=entity.refund_type.value
            if hasattr(entity.refund_type, "value")
            else str(entity.refund_type),
            status=entity.status.value
            if hasattr(entity.status, "value")
            else str(entity.status),
            reason=entity.reason.value
            if hasattr(entity.reason, "value")
            else str(entity.reason),
            reason_note=entity.reason_note,
            amount=entity.amount,
            currency=entity.currency,
            payment_provider=entity.payment_provider,
            payment_id=entity.payment_id,
            provider_refund_id=entity.provider_refund_id,
            requested_by=entity.requested_by,
            approved_by=entity.approved_by,
            rejected_by=entity.rejected_by,
            processed_at=entity.processed_at,
            completed_at=entity.completed_at,
            rejected_at=entity.rejected_at,
            failure_reason=entity.failure_reason,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class RefundListItemDTO(BaseDTO):
    """Refund list item data transfer object (summary)."""

    id: UUID
    refund_number: str
    order_id: UUID
    order_number: str | None
    refund_type: str
    status: str
    reason: str
    amount: int
    currency: str
    created_at: datetime

    @classmethod
    def from_entity(
        cls, entity: Refund, order_number: str | None = None
    ) -> "RefundListItemDTO":
        """Create DTO from Refund entity."""
        return cls(
            id=entity.id,
            refund_number=entity.refund_number,
            order_id=entity.order_id,
            order_number=order_number,
            refund_type=entity.refund_type.value
            if hasattr(entity.refund_type, "value")
            else str(entity.refund_type),
            status=entity.status.value
            if hasattr(entity.status, "value")
            else str(entity.status),
            reason=entity.reason.value
            if hasattr(entity.reason, "value")
            else str(entity.reason),
            amount=entity.amount,
            currency=entity.currency,
            created_at=entity.created_at,
        )
