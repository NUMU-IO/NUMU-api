"""Create address use case."""

from uuid import UUID

from src.application.dto.customer import CreateAddressDTO, CustomerAddressDTO
from src.core.entities.address import AddressLabel, CustomerAddress
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.address_repository import (
    ICustomerAddressRepository,
)
from src.core.interfaces.repositories.customer_repository import ICustomerRepository


class CreateAddressUseCase:
    """Use case for creating a new customer address."""

    def __init__(
        self,
        customer_repository: ICustomerRepository,
        address_repository: ICustomerAddressRepository,
    ) -> None:
        self.customer_repository = customer_repository
        self.address_repository = address_repository

    async def execute(
        self, customer_id: UUID, dto: CreateAddressDTO, tenant_id: UUID
    ) -> CustomerAddressDTO:
        """Create a new address for a customer."""
        # Verify customer exists
        customer = await self.customer_repository.get_by_id(customer_id)
        if not customer:
            raise EntityNotFoundError("Customer", str(customer_id))

        # If this is the first address or marked as default,
        # unset any existing default
        if dto.is_default:
            existing_default = await self.address_repository.get_default(customer_id)
            if existing_default:
                existing_default.unset_default()
                await self.address_repository.update(existing_default)

        # Create address entity
        label = AddressLabel(dto.label) if dto.label else AddressLabel.HOME
        address = CustomerAddress(
            customer_id=customer_id,
            first_name=dto.first_name,
            last_name=dto.last_name,
            address_line1=dto.address_line1,
            address_line2=dto.address_line2,
            city=dto.city,
            state=dto.state,
            postal_code=dto.postal_code,
            country=dto.country,
            phone=dto.phone,
            is_default=dto.is_default,
            label=label,
            latitude=dto.latitude,
            longitude=dto.longitude,
            location_accuracy=dto.location_accuracy,
            location_source=dto.location_source,
            geocoded_address=dto.geocoded_address,
        )

        # Save address
        created_address = await self.address_repository.create(address, tenant_id)

        # If this is the first address, set it as default
        count = await self.address_repository.count_by_customer(customer_id)
        if count == 1:
            await self.address_repository.set_default(customer_id, created_address.id)
            await self.customer_repository.update_default_address(
                customer_id, created_address.id
            )
            created_address.is_default = True

        return CustomerAddressDTO.from_entity(created_address)
