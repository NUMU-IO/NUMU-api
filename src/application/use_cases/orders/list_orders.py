"""List orders use case."""

from dataclasses import dataclass
from uuid import UUID

from src.application.dto.order import OrderListItemDTO
from src.core.entities.order import OrderStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.customer_repository import ICustomerRepository


@dataclass
class ListOrdersResult:
    """Result of list orders operation."""

    orders: list[OrderListItemDTO]
    total: int
    page: int
    limit: int
    total_pages: int


class ListOrdersUseCase:
    """Use case for listing orders."""

    def __init__(
        self,
        order_repository: IOrderRepository,
        store_repository: IStoreRepository,
        customer_repository: ICustomerRepository,
    ) -> None:
        self.order_repository = order_repository
        self.store_repository = store_repository
        self.customer_repository = customer_repository

    async def execute(
        self,
        store_id: UUID,
        user_id: UUID,
        page: int = 1,
        limit: int = 20,
        status: str | None = None,
        search: str | None = None,
    ) -> ListOrdersResult:
        """List orders for a store."""
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to view orders in this store")

        # Parse status filter
        order_status = None
        if status:
            try:
                order_status = OrderStatus(status)
            except ValueError:
                pass  # Invalid status, ignore filter

        # Calculate pagination
        skip = (page - 1) * limit

        # Get orders
        if search:
            orders = await self.order_repository.search(store_id, search, skip, limit)
            # For search, we don't have a count method, estimate from results
            total = len(orders) if len(orders) < limit else skip + limit + 1
        else:
            orders = await self.order_repository.get_by_store(store_id, skip, limit, order_status)
            total = await self.order_repository.count_by_store(store_id, order_status)

        # Get customer names for orders
        customer_names: dict[UUID, str] = {}
        customer_ids = {order.customer_id for order in orders}
        for customer_id in customer_ids:
            customer = await self.customer_repository.get_by_id(customer_id)
            if customer:
                customer_names[customer_id] = f"{customer.first_name} {customer.last_name}"

        # Convert to DTOs
        order_dtos = [
            OrderListItemDTO.from_entity(order, customer_names.get(order.customer_id))
            for order in orders
        ]

        total_pages = (total + limit - 1) // limit

        return ListOrdersResult(
            orders=order_dtos,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )
