"""Social post repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.social_post import SocialPost
from src.core.interfaces.repositories.base import BaseRepository


class ISocialPostRepository(BaseRepository[SocialPost]):
    """Social post repository interface."""

    @abstractmethod
    async def get_by_connection(
        self,
        connection_id: UUID,
        skip: int = 0,
        limit: int = 200,
    ) -> list[SocialPost]:
        """Get all posts for a connection."""
        ...

    @abstractmethod
    async def get_by_platform_post_id(
        self,
        connection_id: UUID,
        platform_post_id: str,
    ) -> SocialPost | None:
        """Get post by its platform-specific ID within a connection."""
        ...

    @abstractmethod
    async def get_unimported(
        self,
        connection_id: UUID,
        skip: int = 0,
        limit: int = 200,
    ) -> list[SocialPost]:
        """Get posts that haven't been imported yet."""
        ...

    @abstractmethod
    async def mark_imported(
        self,
        post_id: UUID,
        product_id: UUID,
    ) -> SocialPost | None:
        """Mark a post as imported and link it to a product."""
        ...
