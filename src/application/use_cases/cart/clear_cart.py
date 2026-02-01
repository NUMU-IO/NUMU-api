"""Clear cart use case."""

from uuid import UUID

from src.application.dto.cart import CartDTO, CartOperationResultDTO
from src.core.entities.cart import Cart
from src.core.interfaces.repositories.cart_repository import ICartRepository


class ClearCartUseCase:
    """Use case for clearing all items from a shopping cart."""

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
    ) -> CartOperationResultDTO:
        """Clear all items from the cart.

        Args:
            session_id: The session identifier.
            store_id: The store UUID.
            customer_id: Optional customer UUID for authenticated users.

        Returns:
            CartOperationResultDTO with result and empty cart.
        """
        # Get cart
        cart = None
        if customer_id:
            cart = await self.cart_repository.get_by_customer_id(customer_id, store_id)

        if not cart:
            cart = await self.cart_repository.get_by_session_id(session_id, store_id)

        if not cart:
            # Create a new empty cart
            cart = Cart(
                session_id=session_id,
                store_id=store_id,
                customer_id=customer_id,
            )
        else:
            # Clear existing cart
            cart.clear()

        # Save cart
        cart = await self.cart_repository.save(cart)

        return CartOperationResultDTO(
            success=True,
            cart=CartDTO.from_entity(cart),
            message="Cart cleared successfully",
        )
