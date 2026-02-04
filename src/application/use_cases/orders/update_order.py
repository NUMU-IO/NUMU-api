"""Update order use case."""

from uuid import UUID

from src.application.dto.order import OrderDTO, UpdateOrderDTO
from src.core.entities.order import OrderShippingAddress
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class UpdateOrderUseCase:
    """Use case for updating an order."""

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
        dto: UpdateOrderDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> OrderDTO:
        """Update an order."""
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to update orders in this store"
            )

        # Get order
        order = await self.order_repository.get_by_id(order_id)
        if not order:
            raise EntityNotFoundError("Order", str(order_id))

        # Verify order belongs to store
        if order.store_id != store_id:
            raise EntityNotFoundError("Order", str(order_id))

        # Update shipping address if provided
        if dto.shipping_address:
            order.shipping_address = OrderShippingAddress(
                first_name=dto.shipping_address.first_name,
                last_name=dto.shipping_address.last_name,
                address_line1=dto.shipping_address.address_line1,
                address_line2=dto.shipping_address.address_line2,
                city=dto.shipping_address.city,
                state=dto.shipping_address.state,
                postal_code=dto.shipping_address.postal_code,
                country=dto.shipping_address.country,
                phone=dto.shipping_address.phone,
            )

        # Update billing address if provided
        if dto.billing_address:
            order.billing_address = OrderShippingAddress(
                first_name=dto.billing_address.first_name,
                last_name=dto.billing_address.last_name,
                address_line1=dto.billing_address.address_line1,
                address_line2=dto.billing_address.address_line2,
                city=dto.billing_address.city,
                state=dto.billing_address.state,
                postal_code=dto.billing_address.postal_code,
                country=dto.billing_address.country,
                phone=dto.billing_address.phone,
            )

        # Update other fields
        if dto.shipping_cost is not None:
            order.shipping_cost = dto.shipping_cost
            # Recalculate total
            order.total = (
                order.subtotal
                + order.shipping_cost
                + order.tax_amount
                - order.discount_amount
            )

        if dto.tax_amount is not None:
            order.tax_amount = dto.tax_amount
            order.total = (
                order.subtotal
                + order.shipping_cost
                + order.tax_amount
                - order.discount_amount
            )

        if dto.discount_amount is not None:
            order.discount_amount = dto.discount_amount
            order.total = (
                order.subtotal
                + order.shipping_cost
                + order.tax_amount
                - order.discount_amount
            )

        if dto.payment_method is not None:
            order.payment_method = dto.payment_method

        if dto.shipping_method is not None:
            order.shipping_method = dto.shipping_method

        if dto.tracking_number is not None:
            order.tracking_number = dto.tracking_number

        if dto.notes is not None:
            order.notes = dto.notes

        if dto.customer_notes is not None:
            order.customer_notes = dto.customer_notes

        order.touch()

        # Save order
        updated_order = await self.order_repository.update(order)

        return OrderDTO.from_entity(updated_order)
