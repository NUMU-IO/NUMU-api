"""Order DTOs."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.order import Order, OrderLineItem, OrderShippingAddress


@dataclass
class OrderLineItemDTO(BaseDTO):
    """Order line item data transfer object."""

    product_id: UUID
    product_name: str
    variant_id: UUID | None
    variant_name: str | None
    sku: str | None
    quantity: int
    unit_price: int
    total_price: int

    @classmethod
    def from_entity(cls, item: OrderLineItem) -> "OrderLineItemDTO":
        """Create DTO from OrderLineItem."""
        return cls(
            product_id=item.product_id,
            product_name=item.product_name,
            variant_id=item.variant_id,
            variant_name=item.variant_name,
            sku=item.sku,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total_price=item.total_price,
        )


@dataclass
class OrderAddressDTO(BaseDTO):
    """Order address data transfer object."""

    first_name: str
    last_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None

    @property
    def full_name(self) -> str:
        """Get full name."""
        return f"{self.first_name} {self.last_name}"

    @classmethod
    def from_entity(cls, address: OrderShippingAddress) -> "OrderAddressDTO":
        """Create DTO from OrderShippingAddress."""
        return cls(
            first_name=address.first_name,
            last_name=address.last_name,
            address_line1=address.address_line1,
            address_line2=address.address_line2,
            city=address.city,
            state=address.state,
            postal_code=address.postal_code,
            country=address.country,
            phone=address.phone,
        )


@dataclass
class OrderDTO(BaseDTO):
    """Order data transfer object."""

    id: UUID
    store_id: UUID
    customer_id: UUID
    order_number: str
    line_items: list[OrderLineItemDTO]
    shipping_address: OrderAddressDTO
    billing_address: OrderAddressDTO | None
    status: str
    payment_status: str
    fulfillment_status: str
    subtotal: int
    shipping_cost: int
    tax_amount: int
    discount_amount: int
    total: int
    currency: str
    payment_method: str | None
    payment_id: str | None
    shipping_method: str | None
    tracking_number: str | None
    tracking_url: str | None
    notes: str | None
    customer_notes: str | None
    item_count: int
    is_paid: bool
    can_be_cancelled: bool
    cancelled_at: datetime | None
    paid_at: datetime | None
    fulfilled_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: Order) -> "OrderDTO":
        """Create DTO from Order entity."""
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            customer_id=entity.customer_id,
            order_number=entity.order_number,
            line_items=[
                OrderLineItemDTO.from_entity(item) for item in entity.line_items
            ],
            shipping_address=OrderAddressDTO.from_entity(entity.shipping_address),
            billing_address=OrderAddressDTO.from_entity(entity.billing_address)
            if entity.billing_address
            else None,
            status=entity.status.value,
            payment_status=entity.payment_status.value,
            fulfillment_status=entity.fulfillment_status.value,
            subtotal=entity.subtotal,
            shipping_cost=entity.shipping_cost,
            tax_amount=entity.tax_amount,
            discount_amount=entity.discount_amount,
            total=entity.total,
            currency=entity.currency,
            payment_method=entity.payment_method,
            payment_id=entity.payment_id,
            shipping_method=entity.shipping_method,
            tracking_number=entity.tracking_number,
            tracking_url=entity.tracking_url,
            notes=entity.notes,
            customer_notes=entity.customer_notes,
            item_count=entity.item_count,
            is_paid=entity.is_paid,
            can_be_cancelled=entity.can_be_cancelled,
            cancelled_at=entity.cancelled_at,
            paid_at=entity.paid_at,
            fulfilled_at=entity.fulfilled_at,
            shipped_at=entity.shipped_at,
            delivered_at=entity.delivered_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class OrderListItemDTO(BaseDTO):
    """Order list item data transfer object (summary)."""

    id: UUID
    order_number: str
    customer_id: UUID
    customer_name: str | None
    status: str
    payment_status: str
    fulfillment_status: str
    total: int
    currency: str
    item_count: int
    payment_method: str | None
    created_at: datetime

    @classmethod
    def from_entity(
        cls, entity: Order, customer_name: str | None = None
    ) -> "OrderListItemDTO":
        """Create DTO from Order entity."""
        return cls(
            id=entity.id,
            order_number=entity.order_number,
            customer_id=entity.customer_id,
            customer_name=customer_name,
            status=entity.status.value,
            payment_status=entity.payment_status.value,
            fulfillment_status=entity.fulfillment_status.value,
            total=entity.total,
            currency=entity.currency,
            item_count=entity.item_count,
            payment_method=entity.payment_method,
            created_at=entity.created_at,
        )


@dataclass
class CreateOrderLineItemDTO(BaseDTO):
    """Create order line item data transfer object."""

    product_id: UUID
    product_name: str
    quantity: int = 1
    unit_price: int = 0
    variant_id: UUID | None = None
    variant_name: str | None = None
    sku: str | None = None


@dataclass
class CreateOrderAddressDTO(BaseDTO):
    """Create order address data transfer object."""

    first_name: str
    last_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None


@dataclass
class CreateOrderDTO(BaseDTO):
    """Create order data transfer object."""

    customer_id: UUID
    line_items: list[CreateOrderLineItemDTO]
    shipping_address: CreateOrderAddressDTO
    billing_address: CreateOrderAddressDTO | None = None
    shipping_cost: int = 0
    tax_amount: int = 0
    discount_amount: int = 0
    currency: str = "EGP"
    payment_method: str | None = None
    shipping_method: str | None = None
    customer_notes: str | None = None


@dataclass
class UpdateOrderDTO(BaseDTO):
    """Update order data transfer object."""

    shipping_address: CreateOrderAddressDTO | None = None
    billing_address: CreateOrderAddressDTO | None = None
    shipping_cost: int | None = None
    tax_amount: int | None = None
    discount_amount: int | None = None
    payment_method: str | None = None
    shipping_method: str | None = None
    tracking_number: str | None = None
    notes: str | None = None
    customer_notes: str | None = None


@dataclass
class UpdateOrderStatusDTO(BaseDTO):
    """Update order status data transfer object."""

    status: str
    reason: str | None = None
