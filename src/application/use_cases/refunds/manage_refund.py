"""Refund management use cases: approve, reject, process, get, list."""

from uuid import UUID

from src.application.dto.base import PaginatedDTO
from src.application.dto.refund import RefundDTO, RefundListItemDTO
from src.config.logging_config import get_logger
from src.core.entities.order import PaymentStatus
from src.core.entities.refund import RefundStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.refund_repository import IRefundRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.services.payment_service import IPaymentService

logger = get_logger(__name__)


class ApproveRefundUseCase:
    """Use case for approving a refund request."""

    def __init__(
        self,
        refund_repository: IRefundRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.refund_repository = refund_repository
        self.store_repository = store_repository

    async def execute(
        self,
        refund_id: UUID,
        store_id: UUID,
        user_id: UUID,
    ) -> RefundDTO:
        """Approve a refund request."""
        log = logger.bind(
            refund_id=str(refund_id),
            store_id=str(store_id),
            user_id=str(user_id),
        )
        log.info("refund_approve_attempt")

        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to approve refunds")

        refund = await self.refund_repository.get_by_id(refund_id)
        if not refund:
            raise EntityNotFoundError("Refund", str(refund_id))
        if refund.store_id != store_id:
            raise EntityNotFoundError("Refund", str(refund_id))

        try:
            refund.approve(user_id)
        except ValueError as e:
            raise ValidationError(str(e))

        updated = await self.refund_repository.update(refund)
        log.info("refund_approved", refund_number=updated.refund_number)
        return RefundDTO.from_entity(updated)


class RejectRefundUseCase:
    """Use case for rejecting a refund request."""

    def __init__(
        self,
        refund_repository: IRefundRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.refund_repository = refund_repository
        self.store_repository = store_repository

    async def execute(
        self,
        refund_id: UUID,
        store_id: UUID,
        user_id: UUID,
        reason: str | None = None,
    ) -> RefundDTO:
        """Reject a refund request."""
        log = logger.bind(
            refund_id=str(refund_id),
            store_id=str(store_id),
            user_id=str(user_id),
        )
        log.info("refund_reject_attempt")

        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to reject refunds")

        refund = await self.refund_repository.get_by_id(refund_id)
        if not refund:
            raise EntityNotFoundError("Refund", str(refund_id))
        if refund.store_id != store_id:
            raise EntityNotFoundError("Refund", str(refund_id))

        try:
            refund.reject(user_id, reason)
        except ValueError as e:
            raise ValidationError(str(e))

        updated = await self.refund_repository.update(refund)
        log.info("refund_rejected", refund_number=updated.refund_number)
        return RefundDTO.from_entity(updated)


class ProcessRefundUseCase:
    """Use case for processing a refund through the payment provider."""

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
        refund_id: UUID,
        store_id: UUID,
        user_id: UUID,
        payment_service: IPaymentService | None = None,
    ) -> RefundDTO:
        """Process a refund through the payment provider."""
        log = logger.bind(
            refund_id=str(refund_id),
            store_id=str(store_id),
            user_id=str(user_id),
        )
        log.info("refund_process_attempt")

        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to process refunds")

        refund = await self.refund_repository.get_by_id(refund_id)
        if not refund:
            raise EntityNotFoundError("Refund", str(refund_id))
        if refund.store_id != store_id:
            raise EntityNotFoundError("Refund", str(refund_id))

        # Start processing
        try:
            refund.start_processing()
        except ValueError as e:
            raise ValidationError(str(e))

        # Save intermediate state
        await self.refund_repository.update(refund)

        # Call payment provider
        if payment_service and refund.payment_id:
            try:
                result = await payment_service.refund_payment(
                    refund.payment_id, refund.amount
                )

                if result.success:
                    refund.mark_processed(result.refund_id)
                    refund.complete()
                    log.info(
                        "refund_processed_successfully",
                        provider_refund_id=result.refund_id,
                    )
                else:
                    refund.mark_failed(result.error_message)
                    log.warning(
                        "refund_processing_failed",
                        error=result.error_message,
                    )
            except Exception as e:
                refund.mark_failed(str(e))
                log.error("refund_processing_error", error=str(e))
        else:
            # No payment service or no payment ID (e.g., COD manual refund)
            refund.mark_processed(None)
            refund.complete()
            log.info("refund_completed_manually")

        # Save final state
        updated = await self.refund_repository.update(refund)

        # Update order payment status if refund completed
        if updated.status == RefundStatus.COMPLETED:
            await self._update_order_payment_status(refund.order_id, log)

        return RefundDTO.from_entity(updated)

    async def _update_order_payment_status(self, order_id: UUID, log) -> None:
        """Update order payment_status based on total refunded amount."""
        order = await self.order_repository.get_by_id(order_id)
        if not order:
            return

        total_refunded = await self.refund_repository.get_total_refunded_for_order(
            order_id
        )

        if total_refunded >= order.total:
            order.payment_status = PaymentStatus.REFUNDED
            log.info("order_fully_refunded", order_id=str(order_id))
        elif total_refunded > 0:
            order.payment_status = PaymentStatus.PARTIALLY_REFUNDED
            log.info(
                "order_partially_refunded",
                order_id=str(order_id),
                total_refunded=total_refunded,
            )

        order.touch()
        await self.order_repository.update(order)


class GetRefundUseCase:
    """Use case for getting a refund by ID."""

    def __init__(
        self,
        refund_repository: IRefundRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.refund_repository = refund_repository
        self.store_repository = store_repository

    async def execute(
        self,
        refund_id: UUID,
        store_id: UUID,
        user_id: UUID,
    ) -> RefundDTO:
        """Get a refund by ID."""
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to view this refund")

        refund = await self.refund_repository.get_by_id(refund_id)
        if not refund:
            raise EntityNotFoundError("Refund", str(refund_id))
        if refund.store_id != store_id:
            raise EntityNotFoundError("Refund", str(refund_id))

        return RefundDTO.from_entity(refund)


class ListRefundsUseCase:
    """Use case for listing refunds."""

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
        store_id: UUID,
        user_id: UUID,
        order_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedDTO:
        """List refunds for a store, optionally filtered by order."""
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to list refunds")

        skip = (page - 1) * page_size

        # Parse status filter
        refund_status = None
        if status:
            try:
                refund_status = RefundStatus(status)
            except ValueError:
                raise ValidationError(f"Invalid refund status: {status}")

        if order_id:
            # Verify order belongs to store
            order = await self.order_repository.get_by_id(order_id)
            if not order or order.store_id != store_id:
                raise EntityNotFoundError("Order", str(order_id))

            refunds = await self.refund_repository.get_by_order(
                order_id, skip=skip, limit=page_size
            )
            total = await self.refund_repository.count_by_order(order_id)
        else:
            refunds = await self.refund_repository.get_by_store(
                store_id, status=refund_status, skip=skip, limit=page_size
            )
            total = await self.refund_repository.count_by_store(
                store_id, status=refund_status
            )

        # Enrich with order numbers
        items = []
        for refund in refunds:
            order = await self.order_repository.get_by_id(refund.order_id)
            order_number = order.order_number if order else None
            items.append(RefundListItemDTO.from_entity(refund, order_number))

        return PaginatedDTO.create(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
