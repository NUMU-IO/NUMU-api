"""Customer repository interface."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.customer import Customer
from src.core.interfaces.repositories.base import BaseRepository
from src.core.value_objects.email import Email


class ICustomerRepository(BaseRepository[Customer]):
    """Customer repository interface."""

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Customer]:
        """Get all customers for a store."""
        ...

    @abstractmethod
    async def get_by_email(self, store_id: UUID, email: Email) -> Customer | None:
        """Get customer by email within a store."""
        ...

    @abstractmethod
    async def email_exists(self, store_id: UUID, email: Email) -> bool:
        """Check if email already exists for a store."""
        ...

    @abstractmethod
    async def get_by_user_id(self, store_id: UUID, user_id: UUID) -> Customer | None:
        """Get customer by user ID within a store."""
        ...

    @abstractmethod
    async def search(
        self,
        store_id: UUID,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Customer]:
        """Search customers by name or email."""
        ...

    @abstractmethod
    async def get_top_customers(
        self,
        store_id: UUID,
        limit: int = 10,
    ) -> list[Customer]:
        """Get top customers by total spent."""
        ...

    @abstractmethod
    async def count_by_store(
        self,
        store_id: UUID,
        date_from: datetime | None = None,
    ) -> int:
        """Get total count of customers for a store."""
        ...

    @abstractmethod
    async def update_password(
        self, customer_id: UUID, password_hash: str
    ) -> Customer | None:
        """Update customer password hash."""
        ...

    @abstractmethod
    async def update_default_address(
        self, customer_id: UUID, address_id: UUID | None
    ) -> Customer | None:
        """Update customer's default address."""
        ...

    @abstractmethod
    async def verify_customer(self, customer_id: UUID) -> Customer | None:
        """Mark customer as verified."""
        ...
