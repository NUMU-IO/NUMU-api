"""Theme update notification repository interface (Phase 5.1)."""

from abc import ABC, abstractmethod
from uuid import UUID

from src.core.entities.theme_update_notification import ThemeUpdateNotification


class IThemeUpdateNotificationRepository(ABC):
    """Persistence contract for theme-version update notifications."""

    @abstractmethod
    async def get_by_id(self, entity_id: UUID) -> ThemeUpdateNotification | None: ...

    @abstractmethod
    async def get_by_store(
        self, store_id: UUID, status: str | None = None
    ) -> list[ThemeUpdateNotification]: ...

    @abstractmethod
    async def get_for_version(
        self, store_id: UUID, to_version_id: UUID
    ) -> ThemeUpdateNotification | None: ...

    @abstractmethod
    async def create(
        self, entity: ThemeUpdateNotification
    ) -> ThemeUpdateNotification: ...

    @abstractmethod
    async def update(
        self, entity: ThemeUpdateNotification
    ) -> ThemeUpdateNotification: ...

    @abstractmethod
    async def delete(self, entity_id: UUID) -> bool: ...
