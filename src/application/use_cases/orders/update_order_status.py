"""Update order status use case."""

from uuid import UUID

from src.application.dto.order import OrderDTO, UpdateOrderStatusDTO
from src.config.logging_config import get_logger
from src.core.entities.order import OrderStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository

logger = get_logger(__name__)


class UpdateOrderStatusUseCase:
    """Use case for updating an order's status."""

    def __init__(
        self,
        order_repository: IOrderRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.order_repository = order_repository
        self.store_repository = store_repository

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
            raise AuthorizationError("You don't have permission to update orders in this store")

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
            log.warning("order_status_update_failed", reason="invalid_transition", error=str(e))
            raise ValidationError(str(e))

        # Save order
        updated_order = await self.order_repository.update(order)

        log.info(
            "order_status_updated",
            old_status=old_status,
            new_status=updated_order.status.value,
            reason=dto.reason,
        )

        return OrderDTO.from_entity(updated_order)
