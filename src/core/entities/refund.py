"""Refund entity representing a refund request for an order."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from src.core.entities.base import BaseEntity


class RefundStatus(StrEnum):
    """Refund status enumeration."""

    REQUESTED = "requested"
    APPROVED = "approved"
    PROCESSING = "processing"
    PROCESSED = "processed"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class RefundReason(StrEnum):
    """Refund reason enumeration."""

    DEFECTIVE = "defective"
    WRONG_ITEM = "wrong_item"
    NOT_AS_DESCRIBED = "not_as_described"
    CUSTOMER_REQUEST = "customer_request"
    DUPLICATE_ORDER = "duplicate_order"
    OTHER = "other"


class RefundType(StrEnum):
    """Refund type enumeration."""

    FULL = "full"
    PARTIAL = "partial"


# Valid status transitions map
VALID_REFUND_TRANSITIONS: dict[RefundStatus, list[RefundStatus]] = {
    RefundStatus.REQUESTED: [RefundStatus.APPROVED, RefundStatus.REJECTED],
    RefundStatus.APPROVED: [RefundStatus.PROCESSING],
    RefundStatus.PROCESSING: [RefundStatus.PROCESSED, RefundStatus.FAILED],
    RefundStatus.PROCESSED: [RefundStatus.COMPLETED],
    RefundStatus.FAILED: [RefundStatus.PROCESSING],  # retry
    RefundStatus.REJECTED: [],  # terminal
    RefundStatus.COMPLETED: [],  # terminal
}


class Refund(BaseEntity):
    """Refund entity with state machine logic."""

    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
        use_enum_values=False,
        populate_by_name=True,
    )

    # References
    order_id: UUID
    store_id: UUID
    tenant_id: UUID | None = None

    # Identification
    refund_number: str = ""

    # Type & status
    refund_type: RefundType = RefundType.FULL
    status: RefundStatus = RefundStatus.REQUESTED
    reason: RefundReason = RefundReason.CUSTOMER_REQUEST
    reason_note: str | None = None

    # Financial
    amount: int = 0  # in cents
    currency: str = "EGP"

    # Payment provider details
    payment_provider: str | None = None  # paymob, fawry, stripe, cod
    payment_id: str | None = None  # original payment transaction ID
    provider_refund_id: str | None = None  # ID returned by provider after refund

    # Actors
    requested_by: UUID | None = None
    approved_by: UUID | None = None
    rejected_by: UUID | None = None

    # Timestamps
    processed_at: datetime | None = None
    completed_at: datetime | None = None
    rejected_at: datetime | None = None

    # Failure tracking
    failure_reason: str | None = None

    # Metadata (status history, notes, etc.)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # --- Properties ---

    @property
    def can_be_approved(self) -> bool:
        """Check if refund can be approved."""
        return self.status == RefundStatus.REQUESTED

    @property
    def can_be_rejected(self) -> bool:
        """Check if refund can be rejected."""
        return self.status == RefundStatus.REQUESTED

    @property
    def can_be_processed(self) -> bool:
        """Check if refund can be processed (sent to payment provider)."""
        return self.status == RefundStatus.APPROVED

    @property
    def can_be_retried(self) -> bool:
        """Check if a failed refund can be retried."""
        return self.status == RefundStatus.FAILED

    @property
    def is_terminal(self) -> bool:
        """Check if refund is in a terminal state."""
        return self.status in (RefundStatus.COMPLETED, RefundStatus.REJECTED)

    # --- State transitions ---

    def _validate_transition(self, new_status: RefundStatus) -> None:
        """Validate a status transition."""
        valid = VALID_REFUND_TRANSITIONS.get(self.status, [])
        if new_status not in valid:
            raise ValueError(
                f"Cannot transition refund from {self.status.value} to {new_status.value}"
            )

    def _record_transition(
        self, from_status: str, to_status: str, reason: str | None = None
    ) -> None:
        """Record a status transition in metadata."""
        history = self.metadata.get("status_history", [])
        history.append({
            "from": from_status,
            "to": to_status,
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": reason,
        })
        self.metadata = {**self.metadata, "status_history": history}

    def approve(self, user_id: UUID) -> None:
        """Approve the refund request."""
        self._validate_transition(RefundStatus.APPROVED)
        old_status = self.status.value
        self.status = RefundStatus.APPROVED
        self.approved_by = user_id
        self._record_transition(old_status, self.status.value)
        self.touch()

    def reject(self, user_id: UUID, reason: str | None = None) -> None:
        """Reject the refund request."""
        self._validate_transition(RefundStatus.REJECTED)
        old_status = self.status.value
        self.status = RefundStatus.REJECTED
        self.rejected_by = user_id
        self.rejected_at = datetime.now(UTC)
        if reason:
            self.metadata = {**self.metadata, "rejection_reason": reason}
        self._record_transition(old_status, self.status.value, reason)
        self.touch()

    def start_processing(self) -> None:
        """Begin processing the refund with the payment provider."""
        if self.status == RefundStatus.FAILED:
            # Allow retry from failed state
            self._validate_transition(RefundStatus.PROCESSING)
            old_status = self.status.value
            self.status = RefundStatus.PROCESSING
            self.failure_reason = None  # clear previous failure
            self._record_transition(old_status, self.status.value, "retry")
        else:
            self._validate_transition(RefundStatus.PROCESSING)
            old_status = self.status.value
            self.status = RefundStatus.PROCESSING
            self._record_transition(old_status, self.status.value)
        self.touch()

    def mark_processed(self, provider_refund_id: str | None = None) -> None:
        """Mark refund as processed by payment provider."""
        self._validate_transition(RefundStatus.PROCESSED)
        old_status = self.status.value
        self.status = RefundStatus.PROCESSED
        self.provider_refund_id = provider_refund_id
        self.processed_at = datetime.now(UTC)
        self._record_transition(old_status, self.status.value)
        self.touch()

    def complete(self) -> None:
        """Mark refund as completed."""
        self._validate_transition(RefundStatus.COMPLETED)
        old_status = self.status.value
        self.status = RefundStatus.COMPLETED
        self.completed_at = datetime.now(UTC)
        self._record_transition(old_status, self.status.value)
        self.touch()

    def mark_failed(self, reason: str | None = None) -> None:
        """Mark refund as failed."""
        self._validate_transition(RefundStatus.FAILED)
        old_status = self.status.value
        self.status = RefundStatus.FAILED
        self.failure_reason = reason
        self._record_transition(old_status, self.status.value, reason)
        self.touch()
