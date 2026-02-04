"""In-memory Two-Factor Authentication repository.

This is a temporary implementation for development/testing.
For production, replace with a proper database-backed repository.
"""

from uuid import UUID

from src.core.entities.two_factor import TwoFactorAuth, TwoFactorStatus
from src.core.interfaces.repositories.two_factor_repository import ITwoFactorRepository


class InMemoryTwoFactorRepository(ITwoFactorRepository):
    """In-memory implementation of ITwoFactorRepository.

    WARNING: This is for development/testing only.
    Data is lost when the application restarts.
    """

    def __init__(self) -> None:
        self._storage: dict[UUID, TwoFactorAuth] = {}

    async def get_by_id(self, entity_id: UUID) -> TwoFactorAuth | None:
        """Get 2FA by ID."""
        return self._storage.get(entity_id)

    async def get_by_user_id(self, user_id: UUID) -> TwoFactorAuth | None:
        """Get 2FA configuration by user ID."""
        for entity in self._storage.values():
            if entity.user_id == user_id:
                return entity
        return None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[TwoFactorAuth]:
        """Get all 2FA entities with pagination."""
        items = list(self._storage.values())
        return items[skip : skip + limit]

    async def create(self, entity: TwoFactorAuth) -> TwoFactorAuth:
        """Create a new 2FA entity."""
        self._storage[entity.id] = entity
        return entity

    async def update(self, entity: TwoFactorAuth) -> TwoFactorAuth:
        """Update an existing 2FA entity."""
        if entity.id not in self._storage:
            raise ValueError(f"Entity with id {entity.id} not found")
        self._storage[entity.id] = entity
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a 2FA entity by ID."""
        if entity_id in self._storage:
            del self._storage[entity_id]
            return True
        return False

    async def delete_by_user_id(self, user_id: UUID) -> bool:
        """Delete 2FA configuration by user ID."""
        to_delete = None
        for entity_id, entity in self._storage.items():
            if entity.user_id == user_id:
                to_delete = entity_id
                break
        if to_delete:
            del self._storage[to_delete]
            return True
        return False

    async def user_has_2fa_enabled(self, user_id: UUID) -> bool:
        """Check if a user has 2FA enabled."""
        entity = await self.get_by_user_id(user_id)
        return entity is not None and entity.status == TwoFactorStatus.ENABLED

    async def count(self) -> int:
        """Get total count of 2FA entities."""
        return len(self._storage)


# Singleton instance for dependency injection
# Replace with proper database repository in production
_two_factor_repository = InMemoryTwoFactorRepository()


def get_two_factor_repository() -> InMemoryTwoFactorRepository:
    """Get the 2FA repository instance."""
    return _two_factor_repository
