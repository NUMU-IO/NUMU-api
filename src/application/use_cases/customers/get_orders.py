"""Get customer orders use case."""

from uuid import UUID

from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.order_repository import IOrderRepository


class GetCustomerOrdersUseCase:
    """Use case for getting a customer's order history."""

    def __init__(
        self,
        customer_repository: ICustomerRepository,
        order_repository: IOrderRepository,
    ) -> None:
        self.customer_repository = customer_repository
        self.order_repository = order_repository

    async def execute(
        self, customer_id: UUID, skip: int = 0, limit: int = 20
    ) -> dict:
        """Get customer orders with pagination."""
        customer = await self.customer_repository.get_by_id(customer_id)

        if not customer:
            raise EntityNotFoundError("Customer", str(customer_id))

        orders = await self.order_repository.get_by_customer(
            customer_id=customer_id,
            skip=skip,
            limit=limit,
        )

        total = await self.order_repository.count_by_customer(customer_id)

        return {
            "orders": orders,
            "total": total,
            "skip": skip,
            "limit": limit,
        }
