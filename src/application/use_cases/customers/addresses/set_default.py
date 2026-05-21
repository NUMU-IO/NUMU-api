"""Set default address use case."""

from uuid import UUID

from src.application.dto.customer import CustomerAddressDTO
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.address_repository import (
    ICustomerAddressRepository,
)
from src.core.interfaces.repositories.customer_repository import ICustomerRepository


class SetDefaultAddressUseCase:
    """Use case for setting an address as default."""

    def __init__(
        self,
        customer_repository: ICustomerRepository,
        address_repository: ICustomerAddressRepository,
    ) -> None:
        self.customer_repository = customer_repository
        self.address_repository = address_repository

    async def execute(self, customer_id: UUID, address_id: UUID) -> CustomerAddressDTO:
        """Set an address as default."""
        address = await self.address_repository.get_by_id(address_id)

        if not address:
            raise EntityNotFoundError("Address", str(address_id))

        # Verify address belongs to customer
        if address.customer_id != customer_id:
            raise AuthorizationError("Address does not belong to this customer")

        # Set as default (this will unset any previous default)
        updated_address = await self.address_repository.set_default(
            customer_id, address_id
        )

        # Update customer's default_address_id
        await self.customer_repository.update_default_address(customer_id, address_id)

        if not updated_address:
            raise EntityNotFoundError("Address", str(address_id))

        return CustomerAddressDTO.from_entity(updated_address)
