"""Get cart use case."""

from uuid import UUID

from src.application.dto.cart import CartDTO
from src.core.entities.cart import Cart
from src.core.interfaces.repositories.cart_repository import ICartRepository


class GetCartUseCase:
    """Use case for retrieving a shopping cart."""

    def __init__(self, cart_repository: ICartRepository) -> None:
        """Initialize use case.

        Args:
            cart_repository: Cart repository instance.
        """
        self.cart_repository = cart_repository

    async def execute(
        self,
        session_id: str,
        store_id: UUID,
        customer_id: UUID | None = None,
    ) -> CartDTO:
        """Get or create a cart for the session.

        If a customer_id is provided and the customer has an existing cart,
        that cart is returned. Otherwise, returns the session cart or creates
        a new empty cart.

        Args:
            session_id: The session identifier.
            store_id: The store UUID.
            customer_id: Optional customer UUID for authenticated users.

        Returns:
            CartDTO with cart data.
        """
        cart = None

        # Try to get customer cart first if authenticated
        if customer_id:
            cart = await self.cart_repository.get_by_customer_id(customer_id, store_id)

        # Fall back to session cart
        if not cart:
            cart = await self.cart_repository.get_by_session_id(session_id, store_id)

        # Create new cart if none exists
        if not cart:
            cart = Cart(
                session_id=session_id,
                store_id=store_id,
                customer_id=customer_id,
            )
            cart = await self.cart_repository.save(cart)

        return CartDTO.from_entity(cart)
