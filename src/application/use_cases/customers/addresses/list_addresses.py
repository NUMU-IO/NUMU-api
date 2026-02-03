"""List addresses use case."""

from uuid import UUID

from src.application.dto.customer import CustomerAddressDTO
from src.core.interfaces.repositories.address_repository import (
    ICustomerAddressRepository,
)


class ListAddressesUseCase:
    """Use case for listing customer addresses."""

    def __init__(self, address_repository: ICustomerAddressRepository) -> None:
        self.address_repository = address_repository

    async def execute(
        self, customer_id: UUID, skip: int = 0, limit: int = 100
    ) -> list[CustomerAddressDTO]:
        """List all addresses for a customer."""
        addresses = await self.address_repository.get_by_customer(
            customer_id, skip=skip, limit=limit
        )
        return [CustomerAddressDTO.from_entity(addr) for addr in addresses]
