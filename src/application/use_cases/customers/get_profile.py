"""Get customer profile use case."""

from uuid import UUID

from src.application.dto.customer import CustomerDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository


class GetCustomerProfileUseCase:
    """Use case for getting a customer's profile."""

    def __init__(self, customer_repository: ICustomerRepository) -> None:
        self.customer_repository = customer_repository

    async def execute(self, customer_id: UUID) -> CustomerDTO:
        """Get customer profile by ID."""
        customer = await self.customer_repository.get_by_id(customer_id)

        if not customer:
            raise EntityNotFoundError("Customer", str(customer_id))

        return CustomerDTO.from_entity(customer)
