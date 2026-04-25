"""Update order status use case."""

from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
    )

logger = get_logger(__name__)


# Maps a terminal COD outcome status to the network event_type used by
# `write_network_event`. Other statuses don't fire a network event.
_NETWORK_EVENT_FOR_STATUS: dict[OrderStatus, str] = {
    OrderStatus.DELIVERED: "delivery",
    OrderStatus.RETURNED: "rto",
}


class UpdateOrderStatusUseCase:
    """Use case for updating an order's status.

    After persisting the status change, publishes an OrderStatusChangedEvent
    to the event bus. All downstream side-effects (email, WhatsApp, activity
    log, webhooks) are handled by event handlers asynchronously.

    For COD orders transitioning into DELIVERED or RETURNED, also writes
    a cross-merchant trust-network event so manual-ship merchants (no
    Bosta integration) feed signals into ``network_reputation``.
    Idempotent via ``order.metadata["network_{event}_recorded"]`` so the
    same outcome can't be double-counted by Bosta + manual marks.
    """

    def __init__(
        self,
        order_repository: IOrderRepository,
        store_repository: IStoreRepository,
        customer_repository: ICustomerRepository | None = None,
        event_bus: EventBus | None = None,
        network_repository: "NetworkReputationRepository | None" = None,
    ) -> None:
        self.order_repository = order_repository
        self.store_repository = store_repository
        self.customer_repository = customer_repository
        self.event_bus = event_bus
        self.network_repository = network_repository

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
            elif new_status == OrderStatus.RETURNED:
                order.return_to_origin(dto.reason)
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

        # Cross-merchant network reputation: fire delivery/RTO event for
        # COD orders. Idempotent + fail-open — never breaks the status
        # update. Bosta webhook also stamps the same flag, so the path
        # that fires first wins.
        await self._record_network_event(updated_order, new_status, log)

        # Publish domain event — all side-effects handled by event handlers
        await self._publish_status_event(
            updated_order, old_status, new_status, store, dto.reason, log
        )

        return OrderDTO.from_entity(updated_order)

    async def _record_network_event(self, order, new_status: OrderStatus, log) -> None:
        """Write a delivery/RTO event to ``network_reputation``.

        Only fires for COD orders transitioning into DELIVERED or
        RETURNED. Idempotent via ``order.metadata`` so the same outcome
        can't be double-counted by Bosta + manual marks.
        """
        event_type = _NETWORK_EVENT_FOR_STATUS.get(new_status)
        if not event_type:
            return
        if order.payment_method != "cod":
            return
        if not self.network_repository:
            return

        flag_key = f"network_{event_type}_recorded"
        metadata = order.metadata or {}
        if metadata.get(flag_key):
            log.debug(
                "network_event_skipped_idempotent",
                event_type=event_type,
                order_id=str(order.id),
            )
            return

        try:
            from src.application.services.network_reputation_service import (
                extract_phone_hash_from_string,
                write_network_event,
            )

            phone = order.shipping_address.phone if order.shipping_address else None
            phone_hash = extract_phone_hash_from_string(phone)
            if not phone_hash:
                return

            await write_network_event(
                phone_hash=phone_hash,
                store_id=order.store_id,
                event_type=event_type,
                network_repo=self.network_repository,
            )

            order.metadata = {**metadata, flag_key: True}
            await self.order_repository.update(order)

            log.info(
                "network_event_recorded",
                event_type=event_type,
                order_id=str(order.id),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning(
                "network_event_record_failed",
                event_type=event_type,
                error=str(exc),
            )

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
