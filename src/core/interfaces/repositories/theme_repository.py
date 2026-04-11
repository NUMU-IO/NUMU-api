"""Theme repository interfaces."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.theme import StoreTheme, Theme, ThemeVersion
from src.core.interfaces.repositories.base import BaseRepository


class IThemeRepository(BaseRepository[Theme]):
    """Interface for global theme registry access."""

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Theme | None:
        """Get a theme by its unique slug."""
        ...

    @abstractmethod
    async def list_published(
        self,
        type_filter: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Theme]:
        """List all published themes, optionally filtered by type."""
        ...

    @abstractmethod
    async def count_published(self, type_filter: str | None = None) -> int:
        """Count published themes."""
        ...

    @abstractmethod
    async def slug_exists(self, slug: str) -> bool:
        """Check if a slug is already taken."""
        ...


class IThemeVersionRepository(BaseRepository[ThemeVersion]):
    """Interface for theme version access."""

    @abstractmethod
    async def get_latest_for_theme(self, theme_id: UUID) -> ThemeVersion | None:
        """Return the version flagged is_latest=True for a given theme."""
        ...

    @abstractmethod
    async def get_by_theme_and_version(
        self, theme_id: UUID, version: str
    ) -> ThemeVersion | None:
        """Look up a specific semver version for a theme."""
        ...

    @abstractmethod
    async def list_for_theme(self, theme_id: UUID) -> list[ThemeVersion]:
        """List all versions for a theme, newest first."""
        ...

    @abstractmethod
    async def get_latest_for_themes(
        self, theme_ids: list[UUID]
    ) -> dict[UUID, ThemeVersion]:
        """Batch-load the latest version for each theme (avoid N+1)."""
        ...


class IStoreThemeRepository(BaseRepository[StoreTheme]):
    """Interface for store-theme installation management."""

    @abstractmethod
    async def get_active_for_store(self, store_id: UUID) -> StoreTheme | None:
        """Return the currently active installation for a store."""
        ...

    @abstractmethod
    async def get_installations_for_store(self, store_id: UUID) -> list[StoreTheme]:
        """Return all installations (active + inactive) for a store."""
        ...

    @abstractmethod
    async def get_installation(
        self, store_id: UUID, installation_id: UUID
    ) -> StoreTheme | None:
        """Get a specific installation, verifying it belongs to the store."""
        ...

    @abstractmethod
    async def deactivate_all_for_store(self, store_id: UUID) -> None:
        """Set is_active=False on all rows for a store."""
        ...

    @abstractmethod
    async def installation_exists(self, store_id: UUID, theme_id: UUID) -> bool:
        """Check if a theme is already installed on this store."""
        ...
