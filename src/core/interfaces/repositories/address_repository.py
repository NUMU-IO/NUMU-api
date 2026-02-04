"""Customer address repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.address import CustomerAddress
from src.core.interfaces.repositories.base import BaseRepository


class ICustomerAddressRepository(BaseRepository[CustomerAddress]):
    """Customer address repository interface."""

    @abstractmethod
    async def get_by_customer(
        self,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CustomerAddress]:
        """Get all addresses for a customer."""
        ...

    @abstractmethod
    async def get_default(self, customer_id: UUID) -> CustomerAddress | None:
        """Get default address for a customer."""
        ...

    @abstractmethod
    async def set_default(
        self, customer_id: UUID, address_id: UUID
    ) -> CustomerAddress | None:
        """Set an address as default, unsetting any previous default."""
        ...

    @abstractmethod
    async def count_by_customer(self, customer_id: UUID) -> int:
        """Get total count of addresses for a customer."""
        ...

    @abstractmethod
    async def delete_by_customer(self, customer_id: UUID) -> int:
        """Delete all addresses for a customer. Returns count of deleted."""
        ...
