"""Cart repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.cart import Cart
from src.core.interfaces.repositories.base import BaseRepository


class ICartRepository(BaseRepository[Cart]):
    """Cart repository interface."""

    @abstractmethod
    async def get_active_cart(
        self,
        store_id: UUID,
        customer_id: UUID,
    ) -> Cart | None:
        """Get the active cart for a customer in a store."""
        ...

    @abstractmethod
    async def get_or_create_cart(
        self,
        store_id: UUID,
        customer_id: UUID,
        tenant_id: UUID,
    ) -> Cart:
        """Get existing cart or create a new one."""
        ...

    @abstractmethod
    async def clear_cart(self, cart_id: UUID) -> None:
        """Remove all items from a cart."""
        ...
