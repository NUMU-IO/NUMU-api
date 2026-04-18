"""Remove from cart use case."""

from uuid import UUID

from src.application.dto.cart import CartDTO, CartOperationResultDTO, RemoveFromCartDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.cart_repository import ICartRepository


class RemoveFromCartUseCase:
    """Use case for removing items from a shopping cart."""

    def __init__(self, cart_repository: ICartRepository) -> None:
        """Initialize use case.

        Args:
            cart_repository: Cart repository instance.
        """
        self.cart_repository = cart_repository

    async def execute(
        self,
        dto: RemoveFromCartDTO,
        session_id: str,
        store_id: UUID,
        customer_id: UUID | None = None,
    ) -> CartOperationResultDTO:
        """Remove an item from the cart.

        Args:
            dto: Remove from cart data.
            session_id: The session identifier.
            store_id: The store UUID.
            customer_id: Optional customer UUID for authenticated users.

        Returns:
            CartOperationResultDTO with result and updated cart.

        Raises:
            EntityNotFoundError: If cart or item not found.
        """
        # Get cart
        cart = None
        if customer_id:
            cart = await self.cart_repository.get_by_customer_id(customer_id, store_id)

        if not cart:
            cart = await self.cart_repository.get_by_session_id(session_id, store_id)

        if not cart:
            raise EntityNotFoundError("Cart", session_id, identifier_name="session_id")

        # Check if item exists in cart
        existing_item = cart.get_item(dto.product_id, dto.variant_id)
        if not existing_item:
            raise EntityNotFoundError(
                "Cart item",
                f"product_id={dto.product_id}, variant_id={dto.variant_id}",
                identifier_name="key",
            )

        product_name = existing_item.product_name

        # Remove item
        cart.remove_item(dto.product_id, dto.variant_id)

        # Save cart
        cart = await self.cart_repository.save(cart)

        return CartOperationResultDTO(
            success=True,
            cart=CartDTO.from_entity(cart),
            message=f"Removed '{product_name}' from cart",
        )
