"""Cache service interface."""

from abc import ABC, abstractmethod
from typing import Any


class ICacheService(ABC):
    """Cache service interface."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Get value from cache."""
        ...

    @abstractmethod
    async def set(
        self,
        key: str,
        value: Any,
        expire: int | None = None,
    ) -> bool:
        """Set value in cache with optional expiration in seconds."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        ...

    @abstractmethod
    async def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern. Returns count of deleted keys."""
        ...

    @abstractmethod
    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        ...

    @abstractmethod
    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values from cache."""
        ...

    @abstractmethod
    async def set_many(
        self,
        mapping: dict[str, Any],
        expire: int | None = None,
    ) -> bool:
        """Set multiple values in cache."""
        ...
