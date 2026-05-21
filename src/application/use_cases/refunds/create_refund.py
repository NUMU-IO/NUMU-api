"""Create refund use case."""

from uuid import UUID

from src.application.dto.refund import CreateRefundDTO, RefundDTO
from src.config.logging_config import get_logger
from src.core.entities.refund import Refund, RefundReason, RefundType
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.refund_repository import IRefundRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository

logger = get_logger(__name__)


class CreateRefundUseCase:
    """Use case for creating a refund request."""

    def __init__(
        self,
        refund_repository: IRefundRepository,
        order_repository: IOrderRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.refund_repository = refund_repository
        self.order_repository = order_repository
        self.store_repository = store_repository

    async def execute(
        self,
        dto: CreateRefundDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> RefundDTO:
        """Create a refund request for an order."""
        log = logger.bind(
            order_id=str(dto.order_id),
            store_id=str(store_id),
            user_id=str(user_id),
            refund_type=dto.refund_type,
        )
        log.info("refund_create_attempt")

        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            log.warning("refund_create_failed", reason="store_not_found")
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            log.warning("refund_create_failed", reason="unauthorized")
            raise AuthorizationError(
                "You don't have permission to create refunds for this store"
            )

        # Get order and validate
        order = await self.order_repository.get_by_id(dto.order_id)
        if not order:
            log.warning("refund_create_failed", reason="order_not_found")
            raise EntityNotFoundError("Order", str(dto.order_id))

        if order.store_id != store_id:
            log.warning("refund_create_failed", reason="order_not_in_store")
            raise EntityNotFoundError("Order", str(dto.order_id))

        # Validate order is paid
        if not order.is_paid:
            log.warning("refund_create_failed", reason="order_not_paid")
            raise ValidationError("Cannot refund an unpaid order")

        # Calculate refundable amount
        already_refunded = await self.refund_repository.get_total_refunded_for_order(
            dto.order_id
        )
        refundable_amount = order.total - already_refunded

        if refundable_amount <= 0:
            log.warning("refund_create_failed", reason="fully_refunded")
            raise ValidationError("Order has already been fully refunded")

        # Validate refund type and amount
        try:
            refund_type = RefundType(dto.refund_type)
        except ValueError:
            raise ValidationError(f"Invalid refund type: {dto.refund_type}")

        try:
            reason = RefundReason(dto.reason)
        except ValueError:
            raise ValidationError(f"Invalid refund reason: {dto.reason}")

        if refund_type == RefundType.FULL:
            amount = refundable_amount
        else:
            # Partial refund
            if dto.amount is None or dto.amount <= 0:
                raise ValidationError("Partial refund requires a positive amount")
            if dto.amount > refundable_amount:
                raise ValidationError(
                    f"Refund amount ({dto.amount}) exceeds refundable amount ({refundable_amount})"
                )
            amount = dto.amount

        # Generate refund number
        refund_number = await self.refund_repository.get_next_refund_number(store_id)

        # Create refund entity
        refund = Refund(
            order_id=dto.order_id,
            store_id=store_id,
            tenant_id=order.tenant_id,
            refund_number=refund_number,
            refund_type=refund_type,
            reason=reason,
            reason_note=dto.reason_note,
            amount=amount,
            currency=order.currency,
            payment_provider=order.payment_method,
            payment_id=order.payment_id,
            requested_by=user_id,
        )

        # Save
        created_refund = await self.refund_repository.create(refund)

        log.info(
            "refund_created",
            refund_id=str(created_refund.id),
            refund_number=created_refund.refund_number,
            amount=created_refund.amount,
        )

        return RefundDTO.from_entity(created_refund)
