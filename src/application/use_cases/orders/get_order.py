"""Get order use case."""

from uuid import UUID

from src.application.dto.order import OrderDTO
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class GetOrderUseCase:
    """Use case for getting a single order."""

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
        store_id: UUID,
        user_id: UUID,
    ) -> OrderDTO:
        """Get an order by ID."""
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view orders in this store"
            )

        # Get order
        order = await self.order_repository.get_by_id(order_id)
        if not order:
            raise EntityNotFoundError("Order", str(order_id))

        # Verify order belongs to store
        if order.store_id != store_id:
            raise EntityNotFoundError("Order", str(order_id))

        return OrderDTO.from_entity(order)
