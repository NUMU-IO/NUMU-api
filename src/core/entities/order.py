"""Order entity representing a customer order."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from src.core.entities.base import BaseEntity
from src.core.value_objects.money import Money


class OrderStatus(str, Enum):
    """Order status enumeration."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, Enum):
    """Payment status enumeration."""

    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    FAILED = "failed"


class FulfillmentStatus(str, Enum):
    """Fulfillment status enumeration."""

    UNFULFILLED = "unfulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"


class OrderLineItem:
    """Order line item value object."""

    def __init__(
        self,
        product_id: UUID,
        product_name: str,
        variant_id: UUID | None = None,
        variant_name: str | None = None,
        sku: str | None = None,
        quantity: int = 1,
        unit_price: int = 0,  # In cents
        total_price: int = 0,  # In cents
        weight: Decimal | None = None,
        properties: dict | None = None,
    ) -> None:
        self.product_id = product_id
        self.product_name = product_name
        self.variant_id = variant_id
        self.variant_name = variant_name
        self.sku = sku
        self.quantity = quantity
        self.unit_price = unit_price
        self.total_price = total_price
        self.weight = weight
        self.properties = properties or {}


class ShippingAddress:
    """Shipping address value object."""

    def __init__(
        self,
        first_name: str,
        last_name: str,
        address_line1: str,
        city: str,
        country: str,
        address_line2: str | None = None,
        state: str | None = None,
        postal_code: str | None = None,
        phone: str | None = None,
    ) -> None:
        self.first_name = first_name
        self.last_name = last_name
        self.address_line1 = address_line1
        self.address_line2 = address_line2
        self.city = city
        self.state = state
        self.postal_code = postal_code
        self.country = country
        self.phone = phone


class Order(BaseEntity):
    """Order entity representing a customer order."""

    def __init__(
        self,
        store_id: UUID,
        customer_id: UUID,
        order_number: str,
        line_items: list[OrderLineItem],
        shipping_address: ShippingAddress,
        billing_address: ShippingAddress | None = None,
        status: OrderStatus = OrderStatus.PENDING,
        payment_status: PaymentStatus = PaymentStatus.PENDING,
        fulfillment_status: FulfillmentStatus = FulfillmentStatus.UNFULFILLED,
        subtotal: int = 0,  # In cents
        shipping_cost: int = 0,  # In cents
        tax_amount: int = 0,  # In cents
        discount_amount: int = 0,  # In cents
        total: int = 0,  # In cents
        currency: str = "USD",
        payment_method: str | None = None,
        payment_id: str | None = None,
        shipping_method: str | None = None,
        tracking_number: str | None = None,
        notes: str | None = None,
        customer_notes: str | None = None,
        metadata: dict | None = None,
        cancelled_at: datetime | None = None,
        paid_at: datetime | None = None,
        fulfilled_at: datetime | None = None,
        id: UUID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(id=id, created_at=created_at, updated_at=updated_at)
        self.store_id = store_id
        self.customer_id = customer_id
        self.order_number = order_number
        self.line_items = line_items
        self.shipping_address = shipping_address
        self.billing_address = billing_address or shipping_address
        self.status = status
        self.payment_status = payment_status
        self.fulfillment_status = fulfillment_status
        self.subtotal = subtotal
        self.shipping_cost = shipping_cost
        self.tax_amount = tax_amount
        self.discount_amount = discount_amount
        self.total = total
        self.currency = currency
        self.payment_method = payment_method
        self.payment_id = payment_id
        self.shipping_method = shipping_method
        self.tracking_number = tracking_number
        self.notes = notes
        self.customer_notes = customer_notes
        self.metadata = metadata or {}
        self.cancelled_at = cancelled_at
        self.paid_at = paid_at
        self.fulfilled_at = fulfilled_at

    def confirm(self) -> None:
        """Confirm the order."""
        self.status = OrderStatus.CONFIRMED
        self.updated_at = datetime.utcnow()

    def mark_as_paid(self, payment_id: str) -> None:
        """Mark order as paid."""
        self.payment_status = PaymentStatus.PAID
        self.payment_id = payment_id
        self.paid_at = datetime.utcnow()
        self.status = OrderStatus.PROCESSING
        self.updated_at = datetime.utcnow()

    def ship(self, tracking_number: str | None = None) -> None:
        """Ship the order."""
        self.status = OrderStatus.SHIPPED
        self.fulfillment_status = FulfillmentStatus.FULFILLED
        self.tracking_number = tracking_number
        self.fulfilled_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def deliver(self) -> None:
        """Mark order as delivered."""
        self.status = OrderStatus.DELIVERED
        self.updated_at = datetime.utcnow()

    def cancel(self) -> None:
        """Cancel the order."""
        self.status = OrderStatus.CANCELLED
        self.cancelled_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def refund(self) -> None:
        """Refund the order."""
        self.status = OrderStatus.REFUNDED
        self.payment_status = PaymentStatus.REFUNDED
        self.updated_at = datetime.utcnow()
