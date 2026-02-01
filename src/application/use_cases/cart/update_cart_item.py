"""Update cart item use case."""

from uuid import UUID

from src.application.dto.cart import CartDTO, CartOperationResultDTO, UpdateCartItemDTO
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.core.interfaces.repositories.product_repository import IProductRepository


class UpdateCartItemUseCase:
    """Use case for updating cart item quantity."""

    def __init__(
        self,
        cart_repository: ICartRepository,
        product_repository: IProductRepository,
    ) -> None:
        """Initialize use case.

        Args:
            cart_repository: Cart repository instance.
            product_repository: Product repository instance.
        """
        self.cart_repository = cart_repository
        self.product_repository = product_repository

    async def execute(
        self,
        dto: UpdateCartItemDTO,
        session_id: str,
        store_id: UUID,
        customer_id: UUID | None = None,
    ) -> CartOperationResultDTO:
        """Update the quantity of an item in the cart.

        If quantity is 0, the item is removed from the cart.

        Args:
            dto: Update cart item data.
            session_id: The session identifier.
            store_id: The store UUID.
            customer_id: Optional customer UUID for authenticated users.

        Returns:
            CartOperationResultDTO with result and updated cart.

        Raises:
            EntityNotFoundError: If cart or item not found.
            ValidationError: If quantity is invalid or insufficient stock.
        """
        # Get cart
        cart = None
        if customer_id:
            cart = await self.cart_repository.get_by_customer_id(customer_id, store_id)

        if not cart:
            cart = await self.cart_repository.get_by_session_id(session_id, store_id)

        if not cart:
            raise EntityNotFoundError("Cart", session_id)

        # Check if item exists in cart
        existing_item = cart.get_item(dto.product_id, dto.variant_id)
        if not existing_item:
            raise EntityNotFoundError(
                "Cart item",
                f"product_id={dto.product_id}, variant_id={dto.variant_id}",
            )

        # If quantity is 0 or less, remove the item
        if dto.quantity <= 0:
            cart.remove_item(dto.product_id, dto.variant_id)
            cart = await self.cart_repository.save(cart)

            return CartOperationResultDTO(
                success=True,
                cart=CartDTO.from_entity(cart),
                message=f"Removed '{existing_item.product_name}' from cart",
            )

        # Validate stock availability for new quantity
        product = await self.product_repository.get_by_id(dto.product_id)
        if not product:
            raise EntityNotFoundError("Product", str(dto.product_id))

        if product.quantity < dto.quantity:
            raise ValidationError(
                f"Insufficient stock for '{product.name}'. "
                f"Available: {product.quantity}, Requested: {dto.quantity}"
            )

        # Update quantity
        cart.update_item_quantity(dto.product_id, dto.quantity, dto.variant_id)

        # Save cart
        cart = await self.cart_repository.save(cart)

        return CartOperationResultDTO(
            success=True,
            cart=CartDTO.from_entity(cart),
            message=f"Updated '{existing_item.product_name}' quantity to {dto.quantity}",
        )
