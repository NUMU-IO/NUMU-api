"""Add to cart use case."""

from uuid import UUID

from src.application.dto.cart import AddToCartDTO, CartDTO, CartOperationResultDTO
from src.core.entities.cart import Cart
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.value_objects.cart_item import CartItem


class AddToCartUseCase:
    """Use case for adding items to a shopping cart."""

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
        dto: AddToCartDTO,
        session_id: str,
        store_id: UUID,
        customer_id: UUID | None = None,
    ) -> CartOperationResultDTO:
        """Add an item to the cart.

        Validates the product exists and has sufficient stock.
        If the item already exists in cart, quantity is increased.

        Args:
            dto: Add to cart data.
            session_id: The session identifier.
            store_id: The store UUID.
            customer_id: Optional customer UUID for authenticated users.

        Returns:
            CartOperationResultDTO with result and updated cart.

        Raises:
            EntityNotFoundError: If product not found.
            ValidationError: If quantity is invalid or insufficient stock.
        """
        # Validate quantity
        if dto.quantity < 1:
            raise ValidationError("Quantity must be at least 1")

        # Fetch the product to validate it exists and get details
        product = await self.product_repository.get_by_id(dto.product_id)
        if not product:
            raise EntityNotFoundError("Product", str(dto.product_id))

        # Check if product belongs to this store
        if product.store_id != store_id:
            raise EntityNotFoundError("Product", str(dto.product_id))

        # Check stock availability
        if not product.is_in_stock:
            raise ValidationError(f"Product '{product.name}' is out of stock")

        if product.quantity < dto.quantity:
            raise ValidationError(
                f"Insufficient stock for '{product.name}'. "
                f"Available: {product.quantity}, Requested: {dto.quantity}"
            )

        # Get or create cart
        cart = await self.cart_repository.get_by_session_id(session_id, store_id)
        if not cart:
            if customer_id:
                cart = await self.cart_repository.get_by_customer_id(
                    customer_id, store_id
                )

        if not cart:
            cart = Cart(
                session_id=session_id,
                store_id=store_id,
                customer_id=customer_id,
            )

        # Check if adding more than available when combined with existing cart qty
        existing_item = cart.get_item(dto.product_id, dto.variant_id)
        if existing_item:
            total_qty = existing_item.quantity + dto.quantity
            if total_qty > product.quantity:
                raise ValidationError(
                    f"Cannot add {dto.quantity} more. "
                    f"Already have {existing_item.quantity} in cart. "
                    f"Available stock: {product.quantity}"
                )

        # Create cart item
        # Get unit price in cents
        unit_price_cents = int(product.price.amount * 100)

        cart_item = CartItem(
            product_id=product.id,
            product_name=product.name,
            variant_id=dto.variant_id,
            variant_name=None,  # TODO: Fetch variant name if variant_id provided
            sku=product.sku,
            quantity=dto.quantity,
            unit_price=unit_price_cents,
            image_url=product.images[0] if product.images else None,
            weight=product.weight,
        )

        # Add to cart
        cart.add_item(cart_item)

        # Save cart
        cart = await self.cart_repository.save(cart)

        return CartOperationResultDTO(
            success=True,
            cart=CartDTO.from_entity(cart),
            message=f"Added {dto.quantity}x '{product.name}' to cart",
        )
