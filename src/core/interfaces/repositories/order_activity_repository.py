"""Order activity repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.order_activity import OrderActivity
from src.core.interfaces.repositories.base import BaseRepository


class IOrderActivityRepository(BaseRepository[OrderActivity]):
    """Order activity repository interface."""

    @abstractmethod
    async def list_by_order(
        self,
        order_id: UUID,
        store_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[OrderActivity], int]:
        """Return (items, total_count) for an order's activities, newest first."""
        ...
