"""Get order timeline use case."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.order import Order, OrderStatus
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.order_repository import IOrderRepository


@dataclass
class OrderTimelineEventDTO(BaseDTO):
    """Order timeline event DTO."""

    status: str
    title: str
    description: str
    timestamp: datetime | None = None
    is_current: bool = False
    is_completed: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class OrderTimelineDTO(BaseDTO):
    """Order timeline DTO."""

    order_id: UUID
    order_number: str
    current_status: str
    events: list[OrderTimelineEventDTO] = field(default_factory=list)


# Status display configuration
STATUS_CONFIG = {
    OrderStatus.PENDING: {
        "title": "Order Placed",
        "description": "Your order has been placed and is awaiting confirmation.",
    },
    OrderStatus.CONFIRMED: {
        "title": "Order Confirmed",
        "description": "Your order has been confirmed and is being prepared.",
    },
    OrderStatus.PROCESSING: {
        "title": "Processing",
        "description": "Your order is being prepared for shipment.",
    },
    OrderStatus.SHIPPED: {
        "title": "Shipped",
        "description": "Your order has been shipped and is on its way.",
    },
    OrderStatus.DELIVERED: {
        "title": "Delivered",
        "description": "Your order has been delivered successfully.",
    },
    OrderStatus.CANCELLED: {
        "title": "Cancelled",
        "description": "This order has been cancelled.",
    },
    OrderStatus.REFUNDED: {
        "title": "Refunded",
        "description": "This order has been refunded.",
    },
    OrderStatus.PAYMENT_FAILED: {
        "title": "Payment Failed",
        "description": "Payment for this order failed.",
    },
}

# Standard order flow (happy path)
STANDARD_FLOW = [
    OrderStatus.PENDING,
    OrderStatus.CONFIRMED,
    OrderStatus.PROCESSING,
    OrderStatus.SHIPPED,
    OrderStatus.DELIVERED,
]


class GetOrderTimelineUseCase:
    """Use case for getting order timeline/history."""

    def __init__(self, order_repository: IOrderRepository) -> None:
        """Initialize use case.

        Args:
            order_repository: Order repository instance.
        """
        self.order_repository = order_repository

    async def execute(
        self,
        order_id: UUID,
        customer_id: UUID | None = None,
        store_id: UUID | None = None,
    ) -> OrderTimelineDTO:
        """Get the timeline for an order.

        Args:
            order_id: The order UUID.
            customer_id: The customer UUID (for authorization - optional).
            store_id: The store UUID (for authorization - optional).

        Returns:
            OrderTimelineDTO with timeline events.

        Raises:
            EntityNotFoundError: If order not found.
            AuthorizationError: If customer doesn't own the order.
        """
        order = await self.order_repository.get_by_id(order_id)
        if not order:
            raise EntityNotFoundError("Order", str(order_id))

        # Authorization check: customer can only view their own orders
        if customer_id is not None and order.customer_id != customer_id:
            raise EntityNotFoundError("Order", str(order_id))

        # Store check: ensure order belongs to the store
        if store_id is not None and order.store_id != store_id:
            raise EntityNotFoundError("Order", str(order_id))

        events = self._build_timeline(order)

        return OrderTimelineDTO(
            order_id=order.id,
            order_number=order.order_number,
            current_status=order.status.value,
            events=events,
        )

    def _build_timeline(self, order: Order) -> list[OrderTimelineEventDTO]:
        """Build timeline events for an order.

        Args:
            order: The order entity.

        Returns:
            List of timeline events.
        """
        events: list[OrderTimelineEventDTO] = []
        current_status = order.status

        # Handle special statuses (cancelled, refunded, payment_failed)
        if current_status in (
            OrderStatus.CANCELLED,
            OrderStatus.REFUNDED,
            OrderStatus.PAYMENT_FAILED,
        ):
            return self._build_special_status_timeline(order)

        # Build standard flow timeline
        # Calculate current_index once before the loop
        current_index = STANDARD_FLOW.index(current_status) if current_status in STANDARD_FLOW else -1

        for i, status in enumerate(STANDARD_FLOW):
            config = STATUS_CONFIG[status]

            is_completed = i < current_index
            is_current = status == current_status
            timestamp = self._get_status_timestamp(order, status)

            events.append(
                OrderTimelineEventDTO(
                    status=status.value,
                    title=config["title"],
                    description=config["description"],
                    timestamp=timestamp,
                    is_current=is_current,
                    is_completed=is_completed or is_current,
                    metadata=self._get_status_metadata(order, status),
                )
            )

        return events

    def _build_special_status_timeline(
        self, order: Order
    ) -> list[OrderTimelineEventDTO]:
        """Build timeline for cancelled/refunded/failed orders.

        Args:
            order: The order entity.

        Returns:
            List of timeline events.
        """
        events: list[OrderTimelineEventDTO] = []

        # Add order placed event
        events.append(
            OrderTimelineEventDTO(
                status=OrderStatus.PENDING.value,
                title=STATUS_CONFIG[OrderStatus.PENDING]["title"],
                description=STATUS_CONFIG[OrderStatus.PENDING]["description"],
                timestamp=order.created_at,
                is_current=False,
                is_completed=True,
            )
        )

        # Add the special status event
        config = STATUS_CONFIG[order.status]
        timestamp = None
        metadata = {}

        if order.status == OrderStatus.CANCELLED:
            timestamp = order.cancelled_at
            if "cancellation_reason" in order.metadata:
                metadata["reason"] = order.metadata["cancellation_reason"]
        elif order.status == OrderStatus.REFUNDED:
            if "refund_reason" in order.metadata:
                metadata["reason"] = order.metadata["refund_reason"]
        elif order.status == OrderStatus.PAYMENT_FAILED:
            if "payment_failure_reason" in order.metadata:
                metadata["reason"] = order.metadata["payment_failure_reason"]

        events.append(
            OrderTimelineEventDTO(
                status=order.status.value,
                title=config["title"],
                description=config["description"],
                timestamp=timestamp,
                is_current=True,
                is_completed=True,
                metadata=metadata,
            )
        )

        return events

    def _get_status_timestamp(
        self, order: Order, status: OrderStatus
    ) -> datetime | None:
        """Get the timestamp for a status.

        Args:
            order: The order entity.
            status: The order status.

        Returns:
            Timestamp if available, None otherwise.
        """
        timestamp_map = {
            OrderStatus.PENDING: order.created_at,
            OrderStatus.CONFIRMED: order.paid_at,
            OrderStatus.PROCESSING: order.paid_at,
            OrderStatus.SHIPPED: order.shipped_at,
            OrderStatus.DELIVERED: order.delivered_at,
        }
        return timestamp_map.get(status)

    def _get_status_metadata(
        self, order: Order, status: OrderStatus
    ) -> dict:
        """Get metadata for a status.

        Args:
            order: The order entity.
            status: The order status.

        Returns:
            Metadata dictionary.
        """
        metadata = {}

        if status == OrderStatus.SHIPPED:
            if order.tracking_number:
                metadata["tracking_number"] = order.tracking_number
            if order.tracking_url:
                metadata["tracking_url"] = order.tracking_url
            if order.shipping_method:
                metadata["shipping_method"] = order.shipping_method

        if status == OrderStatus.PROCESSING:
            if order.payment_method:
                metadata["payment_method"] = order.payment_method
            if order.payment_id:
                metadata["payment_id"] = order.payment_id

        return metadata
