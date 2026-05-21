"""Confirm COD (Cash on Delivery) payment collected via Bosta webhook."""

from src.config.logging_config import get_logger
from src.core.entities.order import Order
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.order_repository import IOrderRepository

logger = get_logger(__name__)


class ConfirmCodPaymentUseCase:
    """Confirms COD payment when Bosta reports delivery completed.

    Called from the Bosta webhook handler when a delivery is marked
    DELIVERED with a COD amount. Updates order status to DELIVERED
    and payment status to PAID.
    """

    def __init__(self, order_repository: IOrderRepository) -> None:
        self.order_repository = order_repository

    async def execute(
        self,
        tracking_number: str,
        cod_amount: int,
    ) -> Order:
        """Confirm COD payment for a delivered order.

        Args:
            tracking_number: Bosta tracking number.
            cod_amount: COD amount collected in piasters (cents).

        Returns:
            Updated Order entity.

        Raises:
            EntityNotFoundError: If no order found for the tracking number.
            ValidationError: If order is in an invalid state for delivery/payment.
        """
        log = logger.bind(
            tracking_number=tracking_number,
            cod_amount=cod_amount,
        )

        order = await self.order_repository.get_by_tracking_number(tracking_number)
        if not order:
            log.warning("cod_confirmation_failed", reason="order_not_found")
            raise EntityNotFoundError(
                "Order", tracking_number, identifier_name="tracking_number"
            )

        log = log.bind(
            order_id=str(order.id),
            order_number=order.order_number,
            current_status=order.status.value,
        )

        # Mark as delivered (validates SHIPPED -> DELIVERED transition)
        try:
            order.deliver()
        except ValueError as e:
            log.warning("cod_delivery_failed", reason=str(e))
            raise ValidationError(str(e))

        # Mark COD payment as collected
        order.mark_as_paid(
            payment_id=f"cod-bosta-{tracking_number}",
            payment_method="cod",
        )
        order.metadata["cod_amount"] = cod_amount
        order.metadata["cod_collected_via"] = "bosta_webhook"
        order.metadata["cod_tracking_number"] = tracking_number

        updated = await self.order_repository.update(order)
        log.info("cod_payment_confirmed", new_status=updated.status.value)
        return updated
