"""Blog + Article repository interfaces."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.blog import Article, Blog
from src.core.interfaces.repositories.base import BaseRepository


class IBlogRepository(BaseRepository[Blog]):
    """Blog (article collection) repository interface."""

    @abstractmethod
    async def get_by_store(
        self, store_id: UUID, include_unpublished: bool = True
    ) -> list[Blog]:
        """All blogs for a store, ordered by handle."""
        ...

    @abstractmethod
    async def get_by_handle(self, store_id: UUID, handle: str) -> Blog | None:
        """A single blog by its handle within a store."""
        ...


class IArticleRepository(BaseRepository[Article]):
    """Article repository interface."""

    @abstractmethod
    async def get_by_blog(
        self,
        blog_id: UUID,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Article]:
        """Articles of a blog (any status unless filtered), newest first."""
        ...

    @abstractmethod
    async def get_by_handle(self, blog_id: UUID, handle: str) -> Article | None:
        """A single article by its current handle within a blog."""
        ...

    @abstractmethod
    async def find_by_previous_handle(
        self, blog_id: UUID, handle: str
    ) -> Article | None:
        """Resolve a RENAMED article by one of its former handles."""
        ...

    @abstractmethod
    async def list_published(
        self, blog_id: UUID, skip: int = 0, limit: int = 100
    ) -> list[Article]:
        """Published articles of a blog, newest published_at first."""
        ...

    @abstractmethod
    async def list_published_for_store(
        self, store_id: UUID, limit: int = 500
    ) -> list[Article]:
        """Published articles across the store (sitemap / feeds)."""
        ...

    @abstractmethod
    async def count_by_blog_for_store(self, store_id: UUID) -> dict[UUID, int]:
        """Article counts (all statuses) grouped by blog for a store."""
        ...

    @abstractmethod
    async def due_scheduled(self, now: datetime, limit: int = 200) -> list[Article]:
        """Scheduled articles whose time has come, ACROSS ALL TENANTS.

        Consumed by the Celery publisher task, which runs with no tenant
        context on purpose — it must promote every store's due articles.
        """
        ...
