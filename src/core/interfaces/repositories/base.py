"""Base repository interface."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from uuid import UUID

from src.core.entities.base import BaseEntity

T = TypeVar("T", bound=BaseEntity)


class BaseRepository(ABC, Generic[T]):
    """Abstract base repository interface."""

    @abstractmethod
    async def get_by_id(self, entity_id: UUID) -> T | None:
        """Get entity by ID."""
        ...

    @abstractmethod
    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[T]:
        """Get all entities with pagination."""
        ...

    @abstractmethod
    async def create(self, entity: T) -> T:
        """Create a new entity."""
        ...

    @abstractmethod
    async def update(self, entity: T) -> T:
        """Update an existing entity."""
        ...

    @abstractmethod
    async def delete(self, entity_id: UUID) -> bool:
        """Delete an entity by ID."""
        ...

    @abstractmethod
    async def count(self) -> int:
        """Get total count of entities."""
        ...
