"""Social connection repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.social_connection import SocialConnection, SocialPlatform
from src.core.interfaces.repositories.base import BaseRepository


class ISocialConnectionRepository(BaseRepository[SocialConnection]):
    """Social connection repository interface."""

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[SocialConnection]:
        """Get all connections for a store."""
        ...

    @abstractmethod
    async def get_by_store_and_platform(
        self,
        store_id: UUID,
        platform: SocialPlatform,
    ) -> SocialConnection | None:
        """Get connection by store and platform."""
        ...
