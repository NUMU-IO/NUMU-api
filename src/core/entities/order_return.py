"""Customer-initiated order return.

Phase 3.1 of the Shopify-parity audit. Sits on top of the existing
Refund machinery — a return is what the *customer* asks for; a refund
is what the *merchant* sends back. Lifecycles connect at the approval
step: when a merchant approves a return, the service layer mints a
matching Refund row and walks it through the existing payment-service
refund pipeline.

Why not collapse into Refund?
    Refund's state machine is admin-side (merchant initiates). A
    customer-side request needs:
      - per-line-item granularity (return only items 2 and 4 of 5)
      - a reason + free-text customer note
      - "received" status separate from "refund processed" (merchant
        verifies the package physically arrived before issuing money)
      - a merchant note that's distinct from the customer note
    Bolting all of that onto Refund would muddle two concerns. Keeping
    them separate maps cleanly onto the lifecycle Shopify exposes.

State diagram:

    requested ──→ approved ──→ received ──→ completed
        │             │             │
        ↓             ↓             ↓
      rejected     rejected      rejected
                                (unusual — covers
                                 fraud after receipt)

When the merchant transitions to `received`, an associated Refund row
is created (linked via Refund.metadata.return_id) and walked through
its own state machine. `completed` here gets set once the linked
Refund reaches its `completed` terminal state.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from src.core.entities.base import BaseEntity


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECEIVED = "received"  # package physically received by merchant
    COMPLETED = "completed"  # refund issued + return closed
    CANCELED = "canceled"  # customer pulled the request before approval


class ReturnReason(StrEnum):
    """Reason buckets, mostly aligned with `RefundReason` so the same
    word lists can drive both UIs."""

    DEFECTIVE = "defective"
    WRONG_ITEM = "wrong_item"
    NOT_AS_DESCRIBED = "not_as_described"
    SIZE_OR_FIT = "size_or_fit"
    NO_LONGER_NEEDED = "no_longer_needed"
    OTHER = "other"


# Allowed transitions. Same shape as VALID_REFUND_TRANSITIONS so the
# state-machine helpers feel familiar; consolidate this layout when we
# add a generic FSM helper.
VALID_RETURN_TRANSITIONS: dict[ReturnStatus, list[ReturnStatus]] = {
    ReturnStatus.REQUESTED: [
        ReturnStatus.APPROVED,
        ReturnStatus.REJECTED,
        ReturnStatus.CANCELED,
    ],
    ReturnStatus.APPROVED: [
        ReturnStatus.RECEIVED,
        ReturnStatus.REJECTED,  # late rejection (rare; e.g. fraud signal post-approval)
    ],
    ReturnStatus.RECEIVED: [ReturnStatus.COMPLETED, ReturnStatus.REJECTED],
    ReturnStatus.REJECTED: [],  # terminal
    ReturnStatus.COMPLETED: [],  # terminal
    ReturnStatus.CANCELED: [],  # terminal
}


class ReturnLineItem(BaseEntity):
    """One line of a return request.

    Order line items are stored as JSONB on the orders table — no
    per-row primary key — so we reference them by their 0-based
    position in `order.line_items`. We persist `unit_price` +
    `product_name` + `product_id` here too so the return survives
    even if the order's line array is later mutated.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
        use_enum_values=False,
    )

    # 0-based position in order.line_items at the time of request.
    order_line_index: int = Field(ge=0)
    product_id: UUID
    variant_id: UUID | None = None
    product_name: str
    quantity: int = Field(gt=0)
    unit_price: int = Field(ge=0, description="Price per unit in cents")
    # Per-line reason override; defaults to the parent return's reason
    # when omitted. Useful when one return spans multiple problems
    # (e.g. one defective, one wrong size).
    reason: ReturnReason | None = None
    customer_note: str | None = None


class OrderReturn(BaseEntity):
    """Customer-initiated return request."""

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
    customer_id: UUID | None = None  # null for guest orders

    # Identification
    return_number: str = ""

    # Items being returned
    line_items: list[ReturnLineItem] = Field(default_factory=list)

    # Status + reason
    status: ReturnStatus = ReturnStatus.REQUESTED
    reason: ReturnReason = ReturnReason.OTHER
    customer_note: str | None = None
    merchant_note: str | None = None

    # Linked refund — populated when the merchant approves and a
    # matching Refund row is minted. Keeps the storefront's "return
    # status" view in sync with the actual refund pipeline.
    refund_id: UUID | None = None

    # Timestamps
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    received_at: datetime | None = None
    completed_at: datetime | None = None
    canceled_at: datetime | None = None

    # Actors
    approved_by: UUID | None = None
    rejected_by: UUID | None = None
    received_by: UUID | None = None

    # Aggregate amount the customer expects back. Computed at request
    # time as sum(line_items[*].unit_price * quantity) so a future
    # product-price change doesn't move the customer's expectation.
    requested_amount: int = 0
    currency: str = "EGP"

    # Free-form metadata: status history, attachments (photos of
    # damaged goods), shipping label tracking.
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Properties ─────────────────────────────────────────────────

    @property
    def can_be_approved(self) -> bool:
        return self.status == ReturnStatus.REQUESTED

    @property
    def can_be_rejected(self) -> bool:
        return self.status in (
            ReturnStatus.REQUESTED,
            ReturnStatus.APPROVED,
            ReturnStatus.RECEIVED,
        )

    @property
    def can_be_received(self) -> bool:
        return self.status == ReturnStatus.APPROVED

    @property
    def can_be_completed(self) -> bool:
        return self.status == ReturnStatus.RECEIVED

    @property
    def can_be_canceled(self) -> bool:
        # Only the customer can cancel, and only before merchant action.
        return self.status == ReturnStatus.REQUESTED

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ReturnStatus.COMPLETED,
            ReturnStatus.REJECTED,
            ReturnStatus.CANCELED,
        )

    # ── Transitions ───────────────────────────────────────────────

    def _validate_transition(self, new_status: ReturnStatus) -> None:
        valid = VALID_RETURN_TRANSITIONS.get(self.status, [])
        if new_status not in valid:
            raise ValueError(
                f"Cannot transition return from {self.status.value} to {new_status.value}"
            )

    def _record_transition(
        self,
        from_status: str,
        to_status: str,
        actor_id: UUID | None = None,
        note: str | None = None,
    ) -> None:
        history = self.metadata.get("status_history", [])
        history.append({
            "from": from_status,
            "to": to_status,
            "timestamp": datetime.now(UTC).isoformat(),
            "actor_id": str(actor_id) if actor_id else None,
            "note": note,
        })
        self.metadata = {**self.metadata, "status_history": history}

    def approve(self, user_id: UUID, merchant_note: str | None = None) -> None:
        self._validate_transition(ReturnStatus.APPROVED)
        old = self.status.value
        self.status = ReturnStatus.APPROVED
        self.approved_by = user_id
        self.approved_at = datetime.now(UTC)
        if merchant_note:
            self.merchant_note = merchant_note
        self._record_transition(old, self.status.value, user_id, merchant_note)
        self.touch()

    def reject(self, user_id: UUID, reason: str | None = None) -> None:
        self._validate_transition(ReturnStatus.REJECTED)
        old = self.status.value
        self.status = ReturnStatus.REJECTED
        self.rejected_by = user_id
        self.rejected_at = datetime.now(UTC)
        if reason:
            self.merchant_note = reason
        self._record_transition(old, self.status.value, user_id, reason)
        self.touch()

    def mark_received(self, user_id: UUID, note: str | None = None) -> None:
        self._validate_transition(ReturnStatus.RECEIVED)
        old = self.status.value
        self.status = ReturnStatus.RECEIVED
        self.received_by = user_id
        self.received_at = datetime.now(UTC)
        self._record_transition(old, self.status.value, user_id, note)
        self.touch()

    def complete(self, refund_id: UUID | None = None) -> None:
        """Mark the return cycle complete; called once the linked Refund
        reaches its terminal `completed` state."""
        self._validate_transition(ReturnStatus.COMPLETED)
        old = self.status.value
        self.status = ReturnStatus.COMPLETED
        self.completed_at = datetime.now(UTC)
        if refund_id:
            self.refund_id = refund_id
        self._record_transition(old, self.status.value)
        self.touch()

    def cancel(self, customer_id: UUID) -> None:
        """Customer-initiated cancel before merchant action."""
        self._validate_transition(ReturnStatus.CANCELED)
        if self.customer_id and self.customer_id != customer_id:
            raise ValueError("Only the requesting customer can cancel a return")
        old = self.status.value
        self.status = ReturnStatus.CANCELED
        self.canceled_at = datetime.now(UTC)
        self._record_transition(old, self.status.value, customer_id)
        self.touch()
