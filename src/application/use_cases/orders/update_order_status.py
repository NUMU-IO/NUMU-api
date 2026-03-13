"""Update order status use case."""

from uuid import UUID

from src.application.dto.order import OrderDTO, UpdateOrderStatusDTO
from src.config.logging_config import get_logger
from src.core.entities.order import OrderStatus
from src.core.events.base import EventBus
from src.core.events.order_events import OrderStatusChangedEvent
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository

logger = get_logger(__name__)


class UpdateOrderStatusUseCase:
    """Use case for updating an order's status.

    After persisting the status change, publishes an OrderStatusChangedEvent
    to the event bus. All downstream side-effects (email, WhatsApp, activity
    log, webhooks) are handled by event handlers asynchronously.
    """

    def __init__(
        self,
        order_repository: IOrderRepository,
        store_repository: IStoreRepository,
        customer_repository: ICustomerRepository | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.order_repository = order_repository
        self.store_repository = store_repository
        self.customer_repository = customer_repository
        self.event_bus = event_bus

    async def execute(
        self,
        order_id: UUID,
        dto: UpdateOrderStatusDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> OrderDTO:
        """Update an order's status."""
        log = logger.bind(
            order_id=str(order_id),
            store_id=str(store_id),
            user_id=str(user_id),
            new_status=dto.status,
        )
        log.info("order_status_update_attempt")

        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            log.warning("order_status_update_failed", reason="store_not_found")
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            log.warning("order_status_update_failed", reason="unauthorized")
            raise AuthorizationError(
                "You don't have permission to update orders in this store"
            )

        # Get order
        order = await self.order_repository.get_by_id(order_id)
        if not order:
            log.warning("order_status_update_failed", reason="order_not_found")
            raise EntityNotFoundError("Order", str(order_id))

        # Verify order belongs to store
        if order.store_id != store_id:
            log.warning("order_status_update_failed", reason="order_not_in_store")
            raise EntityNotFoundError("Order", str(order_id))

        old_status = order.status.value
        log = log.bind(old_status=old_status, order_number=order.order_number)

        # Parse new status
        try:
            new_status = OrderStatus(dto.status)
        except ValueError:
            log.warning("order_status_update_failed", reason="invalid_status")
            raise ValidationError(f"Invalid order status: {dto.status}")

        # Apply status change based on new status
        try:
            if new_status == OrderStatus.CONFIRMED:
                order.confirm()
            elif new_status == OrderStatus.PROCESSING:
                order.start_processing()
            elif new_status == OrderStatus.SHIPPED:
                order.ship()
            elif new_status == OrderStatus.DELIVERED:
                order.deliver()
            elif new_status == OrderStatus.CANCELLED:
                order.cancel(dto.reason)
            elif new_status == OrderStatus.REFUNDED:
                order.refund(dto.reason)
            elif new_status == OrderStatus.PAYMENT_FAILED:
                order.mark_payment_failed(dto.reason)
            else:
                # For other statuses, directly set
                order.status = new_status
                order.touch()
        except ValueError as e:
            log.warning(
                "order_status_update_failed", reason="invalid_transition", error=str(e)
            )
            raise ValidationError(str(e))

        # Save order
        updated_order = await self.order_repository.update(order)

        log.info(
            "order_status_updated",
            old_status=old_status,
            new_status=updated_order.status.value,
            reason=dto.reason,
        )

        # Publish domain event — all side-effects handled by event handlers
        await self._publish_status_event(
            updated_order, old_status, new_status, store, dto.reason, log
        )

        return OrderDTO.from_entity(updated_order)

    async def _publish_status_event(
        self, order, old_status, new_status, store, reason, log
    ):
        """Publish OrderStatusChangedEvent to the event bus.

        Gathers customer context (email, phone, notification preferences)
        and packages it into the event so handlers don't need DB access.
        """
        if not self.event_bus:
            log.debug("event_bus_not_configured")
            return

        try:
            customer = None
            if self.customer_repository:
                customer = await self.customer_repository.get_by_id(order.customer_id)

            customer_email = (
                str(customer.email) if customer and customer.email else None
            )
            customer_phone = (
                str(customer.phone) if customer and customer.phone else None
            )
            customer_name = customer.full_name if customer else None

            prefs = {}
            if customer and customer.metadata:
                prefs = customer.metadata.get("notification_preferences", {})

            event = OrderStatusChangedEvent(
                order_id=order.id,
                order_number=order.order_number,
                store_id=order.store_id,
                store_name=store.name,
                customer_id=order.customer_id,
                customer_email=customer_email,
                customer_phone=customer_phone,
                customer_name=customer_name,
                previous_status=old_status,
                new_status=new_status.value,
                reason=reason,
                tracking_number=order.tracking_number,
                tracking_url=order.tracking_url,
                carrier=order.shipping_method,
                language=store.default_language or "en",
                email_prefs=prefs.get("email", {}),
                whatsapp_prefs=prefs.get("whatsapp", {}),
            )

            self.event_bus.publish(event)

            log.info(
                "order_status_event_published",
                event_id=str(event.event_id),
                status=new_status.value,
            )
        except Exception as e:
            log.error("order_status_event_publish_failed", error=str(e))
