"""Update address use case."""

from uuid import UUID

from src.application.dto.customer import CustomerAddressDTO, UpdateAddressDTO
from src.core.entities.address import AddressLabel
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.address_repository import (
    ICustomerAddressRepository,
)


class UpdateAddressUseCase:
    """Use case for updating a customer address."""

    def __init__(self, address_repository: ICustomerAddressRepository) -> None:
        self.address_repository = address_repository

    async def execute(
        self, customer_id: UUID, address_id: UUID, dto: UpdateAddressDTO
    ) -> CustomerAddressDTO:
        """Update an address."""
        address = await self.address_repository.get_by_id(address_id)

        if not address:
            raise EntityNotFoundError("Address", str(address_id))

        # Verify address belongs to customer
        if address.customer_id != customer_id:
            raise AuthorizationError("Address does not belong to this customer")

        # Update only provided fields
        if dto.first_name is not None:
            address.first_name = dto.first_name
        if dto.last_name is not None:
            address.last_name = dto.last_name
        if dto.address_line1 is not None:
            address.address_line1 = dto.address_line1
        if dto.address_line2 is not None:
            address.address_line2 = dto.address_line2
        if dto.city is not None:
            address.city = dto.city
        if dto.state is not None:
            address.state = dto.state
        if dto.postal_code is not None:
            address.postal_code = dto.postal_code
        if dto.country is not None:
            address.country = dto.country
        if dto.phone is not None:
            address.phone = dto.phone
        if dto.label is not None:
            address.label = AddressLabel(dto.label)

        # Save changes
        updated_address = await self.address_repository.update(address)

        return CustomerAddressDTO.from_entity(updated_address)
