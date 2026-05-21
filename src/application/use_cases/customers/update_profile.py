"""Update customer profile use case."""

from uuid import UUID

from src.application.dto.customer import CustomerDTO, CustomerUpdateProfileDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.value_objects.phone import PhoneNumber


class UpdateCustomerProfileUseCase:
    """Use case for updating a customer's profile."""

    def __init__(self, customer_repository: ICustomerRepository) -> None:
        self.customer_repository = customer_repository

    async def execute(
        self, customer_id: UUID, dto: CustomerUpdateProfileDTO
    ) -> CustomerDTO:
        """Update customer profile."""
        customer = await self.customer_repository.get_by_id(customer_id)

        if not customer:
            raise EntityNotFoundError("Customer", str(customer_id))

        # Update only provided fields
        if dto.first_name is not None:
            customer.first_name = dto.first_name
        if dto.last_name is not None:
            customer.last_name = dto.last_name
        if dto.phone is not None:
            customer.phone = PhoneNumber(value=dto.phone) if dto.phone else None
        if dto.accepts_marketing is not None:
            customer.accepts_marketing = dto.accepts_marketing

        # Save changes
        updated_customer = await self.customer_repository.update(customer)

        return CustomerDTO.from_entity(updated_customer)
