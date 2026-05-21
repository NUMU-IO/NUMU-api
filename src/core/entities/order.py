"""Order entity representing a customer order."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.entities.base import BaseEntity
from src.core.value_objects.money import Currency, Money


class OrderStatus(StrEnum):
    """Order status enumeration."""

    # Merchant-only state. A staff-saved order that isn't billable yet
    # and is invisible to the customer / fulfillment pipeline. Lives
    # here until the merchant clicks "convert" → moves to PENDING.
    DRAFT = "draft"
    # An order awaiting a COD deposit payment. Lives in this state
    # from creation until the gateway webhook confirms the deposit,
    # at which point it transitions to CONFIRMED. If the deposit's
    # `deposit_expires_at` passes without confirmation, a background
    # task transitions it to CANCELLED.
    PENDING_DEPOSIT = "pending_deposit"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    # Customer refused / not handled — used by manual-ship merchants
    # (no Bosta integration) to record an RTO outcome that feeds into
    # the cross-merchant trust network. Distinct from CANCELLED to keep
    # analytics clean: cancellation = ended before shipping; returned =
    # shipped but never collected.
    RETURNED = "returned"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PAYMENT_FAILED = "payment_failed"


# Valid status transitions map
# Key: current status, Value: list of valid next statuses
VALID_STATUS_TRANSITIONS: dict[OrderStatus, list[OrderStatus]] = {
    OrderStatus.DRAFT: [
        # Standard conversion path — merchant clicks "convert to order".
        OrderStatus.PENDING,
        # Merchant deletes the draft.
        OrderStatus.CANCELLED,
    ],
    OrderStatus.PENDING_DEPOSIT: [
        # Deposit paid → order is confirmed; main amount still collected on delivery.
        OrderStatus.CONFIRMED,
        # Customer abandoned / TTL expired / merchant cancelled.
        OrderStatus.CANCELLED,
        # Gateway returned a hard failure (e.g., Paymob declined card).
        OrderStatus.PAYMENT_FAILED,
    ],
    OrderStatus.PENDING: [
        OrderStatus.CONFIRMED,
        OrderStatus.PROCESSING,  # Direct to processing if auto-confirmed
        OrderStatus.CANCELLED,
        OrderStatus.PAYMENT_FAILED,
    ],
    OrderStatus.CONFIRMED: [
        OrderStatus.PROCESSING,
        OrderStatus.CANCELLED,
    ],
    OrderStatus.PROCESSING: [
        OrderStatus.SHIPPED,
        OrderStatus.CANCELLED,  # Can cancel before shipping
    ],
    OrderStatus.SHIPPED: [
        OrderStatus.DELIVERED,
        # Customer refused / merchant ships manually and records RTO.
        OrderStatus.RETURNED,
        # Cannot cancel after shipping — use RETURNED instead.
    ],
    OrderStatus.DELIVERED: [
        OrderStatus.REFUNDED,
    ],
    OrderStatus.CANCELLED: [],  # Terminal state
    OrderStatus.RETURNED: [],  # Terminal state
    OrderStatus.REFUNDED: [],  # Terminal state
    OrderStatus.PAYMENT_FAILED: [
        OrderStatus.PENDING,  # Can retry payment
        OrderStatus.CANCELLED,
    ],
}


class PaymentStatus(StrEnum):
    """Payment status enumeration."""

    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    FAILED = "failed"


class FulfillmentStatus(StrEnum):
    """Fulfillment status enumeration."""

    UNFULFILLED = "unfulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"


class OrderLineItem(BaseModel):
    """Order line item value object.

    Represents a single item in an order with quantity and pricing.
    """

    model_config = ConfigDict(frozen=True)

    product_id: UUID
    product_name: str
    variant_id: UUID | None = None
    variant_name: str | None = None
    sku: str | None = None
    quantity: int = Field(default=1, ge=1)
    unit_price: int = Field(default=0, ge=0)  # In cents
    total_price: int = Field(default=0, ge=0)  # In cents
    weight: Decimal | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @property
    def unit_price_money(self) -> Money:
        """Get unit price as Money object (USD)."""
        return Money.from_cents(self.unit_price)

    @property
    def total_price_money(self) -> Money:
        """Get total price as Money object (USD)."""
        return Money.from_cents(self.total_price)


class OrderShippingAddress(BaseModel):
    """Order shipping/billing address value object.

    Immutable snapshot of address at time of order.
    """

    model_config = ConfigDict(frozen=True)

    first_name: str
    last_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    # Geolocation captured from the storefront map picker. All fields optional
    # for back-compat with orders placed before the feature shipped.
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None  # meters
    location_source: str | None = None  # "gps" | "manual_pin" | None
    geocoded_address: str | None = None  # provider-normalized formatted string

    @property
    def full_name(self) -> str:
        """Get full name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def formatted_address(self) -> str:
        """Get formatted address as single line."""
        parts = [self.address_line1]
        if self.address_line2:
            parts.append(self.address_line2)
        parts.append(self.city)
        if self.state:
            parts.append(self.state)
        if self.postal_code:
            parts.append(self.postal_code)
        parts.append(self.country)
        return ", ".join(parts)


# Backward compatibility alias
ShippingAddress = OrderShippingAddress


class Order(BaseEntity):
    """Order entity representing a customer order.

    Orders go through various status transitions:
    PENDING -> CONFIRMED -> PROCESSING -> SHIPPED -> DELIVERED
    Or: PENDING -> CANCELLED
    Or: DELIVERED -> REFUNDED
    """

    store_id: UUID
    tenant_id: UUID | None = None
    customer_id: UUID
    order_number: str
    line_items: list[OrderLineItem] = Field(default_factory=list)
    shipping_address: OrderShippingAddress
    billing_address: OrderShippingAddress | None = None
    status: OrderStatus = OrderStatus.PENDING
    payment_status: PaymentStatus = PaymentStatus.PENDING
    fulfillment_status: FulfillmentStatus = FulfillmentStatus.UNFULFILLED
    subtotal: int = Field(default=0, ge=0)  # In cents
    shipping_cost: int = Field(default=0, ge=0)  # In cents
    tax_amount: int = Field(default=0, ge=0)  # In cents
    discount_amount: int = Field(default=0, ge=0)  # In cents
    total: int = Field(default=0, ge=0)  # In cents
    currency: str = "USD"
    payment_method: str | None = None
    payment_id: str | None = None
    shipping_method: str | None = None
    # Snapshot of the shipping resolution at checkout time. Nullable so
    # orders placed before the shipping-config system rolls out (or via
    # a legacy flow) still work. Not kept in sync if the zone/rate is
    # edited later — that's the point: history is preserved as it was.
    shipping_zone_id: UUID | None = None
    shipping_rate_id: UUID | None = None
    # ─── COD deposit-to-confirm ─────────────────────────────────────
    # Populated only for orders that went through the deposit flow
    # (COD with store.settings.payment.cod.deposit_policy.enabled=true).
    # `deposit_required_cents` is the amount the merchant's policy asked
    # for at checkout time — snapshotted so later policy edits don't
    # retroactively rewrite what the customer agreed to pay.
    # `deposit_amount_cents` is the amount actually collected (matches
    # `deposit_required_cents` on the happy path — divergence is a
    # discrepancy worth investigating).
    # `balance_due_cents` is computed at read-time as `total - deposit_amount_cents`.
    deposit_required_cents: int | None = None
    deposit_amount_cents: int | None = None
    deposit_paid_at: datetime | None = None
    deposit_expires_at: datetime | None = None
    deposit_gateway: str | None = None  # one of DepositGateway literals
    deposit_payment_id: str | None = None  # external txn id for the deposit
    tracking_number: str | None = None
    tracking_url: str | None = None
    notes: str | None = None
    coupon_code: str | None = None
    coupon_id: UUID | None = None
    customer_notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # UTM attribution tracking
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    # Session fingerprint captured from the storefront so funnel queries
    # (COUNT(DISTINCT session_fingerprint)) can connect this order back to
    # the visitor's earlier page_view / add_to_cart / checkout_started events.
    session_fingerprint: str | None = None
    version: int = 1  # optimistic locking
    cancelled_at: datetime | None = None
    paid_at: datetime | None = None
    fulfilled_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None

    def model_post_init(self, __context: Any) -> None:
        """Set billing address to shipping address if not provided."""
        if self.billing_address is None:
            object.__setattr__(self, "billing_address", self.shipping_address)

    # Properties for Money objects
    @property
    def subtotal_money(self) -> Money:
        """Get subtotal as Money object."""
        return Money.from_cents(self.subtotal, Currency(self.currency))

    @property
    def shipping_cost_money(self) -> Money:
        """Get shipping cost as Money object."""
        return Money.from_cents(self.shipping_cost, Currency(self.currency))

    @property
    def tax_amount_money(self) -> Money:
        """Get tax amount as Money object."""
        return Money.from_cents(self.tax_amount, Currency(self.currency))

    @property
    def discount_amount_money(self) -> Money:
        """Get discount amount as Money object."""
        return Money.from_cents(self.discount_amount, Currency(self.currency))

    @property
    def total_money(self) -> Money:
        """Get total as Money object."""
        return Money.from_cents(self.total, Currency(self.currency))

    # ─── Deposit helpers ─────────────────────────────────────────────

    @property
    def balance_due_cents(self) -> int:
        """Amount still owed by the customer at delivery.

        For deposit orders: `total - deposit_amount_cents` (clamped ≥ 0).
        For everything else: 0 if fully paid, else the full `total`.
        Keeps logic in one place so merchant dashboards, invoices, and
        delivery-side receipt printing read the same number.
        """
        if self.deposit_amount_cents:
            return max(0, self.total - self.deposit_amount_cents)
        return 0 if self.is_paid else self.total

    @property
    def is_deposit_expired(self) -> bool:
        """True when the deposit window has lapsed without a paid deposit.

        The background sweeper and the order-read lazy-expire check both
        use this to decide whether to transition PENDING_DEPOSIT → CANCELLED.
        """
        if self.status != OrderStatus.PENDING_DEPOSIT:
            return False
        if self.deposit_expires_at is None:
            return False
        return datetime.now(UTC) >= self.deposit_expires_at

    # Status check properties
    @property
    def is_pending(self) -> bool:
        """Check if order is pending."""
        return self.status == OrderStatus.PENDING

    @property
    def is_confirmed(self) -> bool:
        """Check if order is confirmed."""
        return self.status == OrderStatus.CONFIRMED

    @property
    def is_processing(self) -> bool:
        """Check if order is processing."""
        return self.status == OrderStatus.PROCESSING

    @property
    def is_shipped(self) -> bool:
        """Check if order is shipped."""
        return self.status == OrderStatus.SHIPPED

    @property
    def is_delivered(self) -> bool:
        """Check if order is delivered."""
        return self.status == OrderStatus.DELIVERED

    @property
    def is_cancelled(self) -> bool:
        """Check if order is cancelled."""
        return self.status == OrderStatus.CANCELLED

    @property
    def is_refunded(self) -> bool:
        """Check if order is refunded."""
        return self.status == OrderStatus.REFUNDED

    @property
    def is_paid(self) -> bool:
        """Check if order is paid."""
        return self.payment_status == PaymentStatus.PAID

    @property
    def is_fulfilled(self) -> bool:
        """Check if order is fully fulfilled."""
        return self.fulfillment_status == FulfillmentStatus.FULFILLED

    @property
    def can_be_cancelled(self) -> bool:
        """Check if order can be cancelled."""
        return self.status in (
            OrderStatus.PENDING_DEPOSIT,
            OrderStatus.PENDING,
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
        )

    @property
    def can_be_refunded(self) -> bool:
        """Check if order can be refunded."""
        return self.is_paid and self.status == OrderStatus.DELIVERED

    @property
    def item_count(self) -> int:
        """Get total number of items in the order."""
        return sum(item.quantity for item in self.line_items)

    # Status transition validation methods
    def can_transition_to(self, new_status: OrderStatus) -> bool:
        """Check if order can transition to the given status.

        Args:
            new_status: The target status.

        Returns:
            True if transition is valid, False otherwise.
        """
        valid_transitions = VALID_STATUS_TRANSITIONS.get(self.status, [])
        return new_status in valid_transitions

    def get_valid_transitions(self) -> list[OrderStatus]:
        """Get list of valid status transitions from current status.

        Returns:
            List of valid target statuses.
        """
        return VALID_STATUS_TRANSITIONS.get(self.status, [])

    def validate_transition(self, new_status: OrderStatus) -> None:
        """Validate that a status transition is allowed.

        Args:
            new_status: The target status.

        Raises:
            ValueError: If transition is not allowed.
        """
        if not self.can_transition_to(new_status):
            valid = [s.value for s in self.get_valid_transitions()]
            raise ValueError(
                f"Invalid status transition: {self.status.value} -> {new_status.value}. "
                f"Valid transitions from {self.status.value}: {valid or 'none (terminal state)'}"
            )

    def transition_to(self, new_status: OrderStatus, reason: str | None = None) -> None:
        """Transition order to a new status with validation.

        Args:
            new_status: The target status.
            reason: Optional reason for the transition.

        Raises:
            ValueError: If transition is not allowed.
        """
        self.validate_transition(new_status)

        old_status = self.status
        self.status = new_status

        # Record transition in metadata
        if "status_history" not in self.metadata:
            self.metadata["status_history"] = []

        self.metadata["status_history"].append({
            "from": old_status.value,
            "to": new_status.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": reason,
        })

        # Update timestamps based on new status
        if new_status == OrderStatus.CANCELLED:
            self.cancelled_at = datetime.now(UTC)
        elif new_status == OrderStatus.RETURNED:
            # Reuse cancelled_at — same semantic of "order ended without
            # payment / fulfillment". Saves a column migration; UI can
            # disambiguate via status.
            self.cancelled_at = datetime.now(UTC)
        elif new_status == OrderStatus.SHIPPED:
            self.shipped_at = datetime.now(UTC)
            self.fulfilled_at = datetime.now(UTC)
            self.fulfillment_status = FulfillmentStatus.FULFILLED
        elif new_status == OrderStatus.DELIVERED:
            self.delivered_at = datetime.now(UTC)
            # COD: cash collected at delivery -> mark as paid
            if (
                self.payment_method == "cod"
                and self.payment_status == PaymentStatus.PENDING
            ):
                self.payment_status = PaymentStatus.PAID
                self.paid_at = datetime.now(UTC)

        self.touch()

    # State transition methods (legacy - now use validate_transition internally)
    def confirm(self) -> None:
        """Confirm the order."""
        if self.status != OrderStatus.PENDING:
            raise ValueError(f"Cannot confirm order in {self.status} status")
        self.status = OrderStatus.CONFIRMED
        self.touch()

    def mark_as_paid(self, payment_id: str, payment_method: str | None = None) -> None:
        """Mark a gateway charge as successful.

        Routes based on current order state:
            * PENDING_DEPOSIT → deposit flow. Records the deposit
              payment_id, sets `deposit_paid_at`, transitions to
              CONFIRMED. `payment_status` STAYS `PENDING` — the main
              order amount is still collected on delivery (COD).
            * Everything else → full-payment flow. Sets `payment_status=PAID`,
              transitions to PROCESSING (existing behaviour).

        Centralizing the branch here means gateway webhook handlers
        don't need to be deposit-aware — they just call `mark_as_paid`
        as before and the entity does the right thing based on state.

        Args:
            payment_id: The payment provider's payment ID
            payment_method: Optional payment method description. For
                deposit orders this is stored on `deposit_gateway`;
                for full-payment orders it's stored on `payment_method`.
        """
        if self.status == OrderStatus.PENDING_DEPOSIT:
            # Deposit flow: the customer has paid the deposit amount.
            # The balance (total - deposit) is still due on delivery.
            self.deposit_paid_at = datetime.now(UTC)
            self.deposit_payment_id = payment_id
            if payment_method:
                self.deposit_gateway = payment_method
            # Transition via the canonical path — enforces allowed
            # transitions and records the change in metadata.
            self.transition_to(
                OrderStatus.CONFIRMED,
                reason=f"deposit_paid via {payment_method or 'unknown'}",
            )
            return
        # Full-payment flow (existing behaviour)
        self.payment_status = PaymentStatus.PAID
        self.payment_id = payment_id
        if payment_method:
            self.payment_method = payment_method
        self.paid_at = datetime.now(UTC)
        self.status = OrderStatus.PROCESSING
        self.touch()

    def mark_payment_failed(self, reason: str | None = None) -> None:
        """Mark payment as failed.

        Args:
            reason: Optional failure reason
        """
        self.payment_status = PaymentStatus.FAILED
        if reason:
            self.metadata["payment_failure_reason"] = reason
        self.touch()

    def start_processing(self) -> None:
        """Start processing the order."""
        if self.status not in (OrderStatus.CONFIRMED, OrderStatus.PENDING):
            raise ValueError(f"Cannot start processing order in {self.status} status")
        self.status = OrderStatus.PROCESSING
        self.touch()

    def ship(
        self, tracking_number: str | None = None, tracking_url: str | None = None
    ) -> None:
        """Ship the order.

        Args:
            tracking_number: Optional tracking number
            tracking_url: Optional tracking URL
        """
        if self.status != OrderStatus.PROCESSING:
            raise ValueError(f"Cannot ship order in {self.status} status")
        self.status = OrderStatus.SHIPPED
        self.fulfillment_status = FulfillmentStatus.FULFILLED
        if tracking_number:
            self.tracking_number = tracking_number
        if tracking_url:
            self.tracking_url = tracking_url
        self.shipped_at = datetime.now(UTC)
        self.fulfilled_at = datetime.now(UTC)
        self.touch()

    def deliver(self) -> None:
        """Mark order as delivered.

        For COD orders, payment_status is automatically set to PAID
        since cash is collected at delivery.
        """
        if self.status != OrderStatus.SHIPPED:
            raise ValueError(f"Cannot deliver order in {self.status} status")
        self.status = OrderStatus.DELIVERED
        self.delivered_at = datetime.now(UTC)
        # COD: cash collected at delivery -> mark as paid
        if (
            self.payment_method == "cod"
            and self.payment_status == PaymentStatus.PENDING
        ):
            self.payment_status = PaymentStatus.PAID
            self.paid_at = datetime.now(UTC)
        self.touch()

    def cancel(self, reason: str | None = None) -> None:
        """Cancel the order.

        Args:
            reason: Optional cancellation reason
        """
        if not self.can_be_cancelled:
            raise ValueError(f"Cannot cancel order in {self.status} status")
        self.status = OrderStatus.CANCELLED
        self.cancelled_at = datetime.now(UTC)
        if reason:
            self.metadata["cancellation_reason"] = reason
        self.touch()

    def return_to_origin(self, reason: str | None = None) -> None:
        """Mark a shipped order as returned (RTO).

        Used by manual-ship merchants (no Bosta integration) to record
        a customer-refused / not-collected outcome so the cross-merchant
        trust network gets the RTO signal. Validates the same way as the
        canonical transition path so SHIPPED → RETURNED is the only
        allowed entry point.
        """
        self.transition_to(OrderStatus.RETURNED, reason=reason)
        if reason:
            self.metadata["return_reason"] = reason

    def refund(self, reason: str | None = None) -> None:
        """Refund the order.

        Args:
            reason: Optional refund reason
        """
        if not self.can_be_refunded:
            raise ValueError("Order cannot be refunded")
        self.status = OrderStatus.REFUNDED
        self.payment_status = PaymentStatus.REFUNDED
        if reason:
            self.metadata["refund_reason"] = reason
        self.touch()

    def partial_refund(self, amount: int, reason: str | None = None) -> None:
        """Process a partial refund.

        Args:
            amount: Refund amount in cents
            reason: Optional refund reason
        """
        if not self.is_paid:
            raise ValueError("Cannot refund unpaid order")
        self.payment_status = PaymentStatus.PARTIALLY_REFUNDED
        self.metadata["partial_refund_amount"] = amount
        if reason:
            self.metadata["partial_refund_reason"] = reason
        self.touch()

    def add_note(self, note: str) -> None:
        """Add an internal note to the order.

        Args:
            note: Note text to add
        """
        if self.notes:
            self.notes = f"{self.notes}\n\n{note}"
        else:
            self.notes = note
        self.touch()

    def update_tracking(
        self, tracking_number: str, tracking_url: str | None = None
    ) -> None:
        """Update tracking information.

        Args:
            tracking_number: New tracking number
            tracking_url: Optional tracking URL
        """
        self.tracking_number = tracking_number
        if tracking_url:
            self.tracking_url = tracking_url
        self.touch()
