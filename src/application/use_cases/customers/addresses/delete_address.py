"""Delete address use case."""

from uuid import UUID

from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.address_repository import ICustomerAddressRepository
from src.core.interfaces.repositories.customer_repository import ICustomerRepository


class DeleteAddressUseCase:
    """Use case for deleting a customer address."""

    def __init__(
        self,
        customer_repository: ICustomerRepository,
        address_repository: ICustomerAddressRepository,
    ) -> None:
        self.customer_repository = customer_repository
        self.address_repository = address_repository

    async def execute(self, customer_id: UUID, address_id: UUID) -> bool:
        """Delete an address."""
        address = await self.address_repository.get_by_id(address_id)

        if not address:
            raise EntityNotFoundError("Address", str(address_id))

        # Verify address belongs to customer
        if address.customer_id != customer_id:
            raise AuthorizationError("Address does not belong to this customer")

        # If this was the default address, we need to clear it from customer
        if address.is_default:
            await self.customer_repository.update_default_address(customer_id, None)

        # Delete the address
        deleted = await self.address_repository.delete(address_id)

        # If there are remaining addresses and we deleted the default,
        # set a new default
        if address.is_default:
            remaining = await self.address_repository.get_by_customer(customer_id, limit=1)
            if remaining:
                await self.address_repository.set_default(customer_id, remaining[0].id)
                await self.customer_repository.update_default_address(
                    customer_id, remaining[0].id
                )

        return deleted
