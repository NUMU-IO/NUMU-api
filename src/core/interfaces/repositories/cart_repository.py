"""Cart repository interface."""

from abc import ABC, abstractmethod
from uuid import UUID

from src.core.entities.cart import Cart


class ICartRepository(ABC):
    """Abstract interface for cart repository.

    Cart repository is session-based and uses Redis for storage
    with TTL (Time To Live) for automatic expiration.
    """

    @abstractmethod
    async def get_by_session_id(
        self,
        session_id: str,
        store_id: UUID,
    ) -> Cart | None:
        """Get cart by session ID and store ID.

        Args:
            session_id: The session identifier.
            store_id: The store UUID.

        Returns:
            Cart if found, None otherwise.
        """
        ...

    @abstractmethod
    async def get_by_customer_id(
        self,
        customer_id: UUID,
        store_id: UUID,
    ) -> Cart | None:
        """Get cart by customer ID and store ID.

        Args:
            customer_id: The customer UUID.
            store_id: The store UUID.

        Returns:
            Cart if found, None otherwise.
        """
        ...

    @abstractmethod
    async def save(self, cart: Cart) -> Cart:
        """Save cart to storage.

        Creates a new cart or updates existing one.
        TTL is automatically set/refreshed on save.

        Args:
            cart: The cart entity to save.

        Returns:
            The saved cart.
        """
        ...

    @abstractmethod
    async def delete(self, session_id: str, store_id: UUID) -> bool:
        """Delete cart by session ID and store ID.

        Args:
            session_id: The session identifier.
            store_id: The store UUID.

        Returns:
            True if deleted, False if not found.
        """
        ...

    @abstractmethod
    async def delete_by_customer_id(
        self,
        customer_id: UUID,
        store_id: UUID,
    ) -> bool:
        """Delete cart by customer ID and store ID.

        Args:
            customer_id: The customer UUID.
            store_id: The store UUID.

        Returns:
            True if deleted, False if not found.
        """
        ...

    @abstractmethod
    async def transfer_to_customer(
        self,
        session_id: str,
        customer_id: UUID,
        store_id: UUID,
    ) -> Cart | None:
        """Transfer a guest cart to a customer.

        Associates a session-based cart with a customer account.
        If customer already has a cart, merges the carts.

        Args:
            session_id: The guest session identifier.
            customer_id: The customer UUID.
            store_id: The store UUID.

        Returns:
            The merged/transferred cart, or None if no guest cart exists.
        """
        ...

    @abstractmethod
    async def extend_ttl(self, session_id: str, store_id: UUID) -> bool:
        """Extend the TTL of a cart.

        Args:
            session_id: The session identifier.
            store_id: The store UUID.

        Returns:
            True if extended, False if cart not found.
        """
        ...
