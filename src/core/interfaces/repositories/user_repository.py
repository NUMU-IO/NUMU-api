"""User repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.user import User
from src.core.interfaces.repositories.base import BaseRepository
from src.core.value_objects.email import Email


class IUserRepository(BaseRepository[User]):
    """User repository interface."""

    @abstractmethod
    async def get_by_email(self, email: Email) -> User | None:
        """Get user by email."""
        ...

    @abstractmethod
    async def get_by_email_str(self, email: str) -> User | None:
        """Get user by email string."""
        ...

    @abstractmethod
    async def email_exists(self, email: Email) -> bool:
        """Check if email already exists."""
        ...

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[User]:
        """Get all users associated with a store."""
        ...
