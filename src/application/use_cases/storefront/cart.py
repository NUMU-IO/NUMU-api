"""Cart use cases for storefront."""

from uuid import UUID

from src.application.dto.cart import AddToCartDTO, CartDTO, CartItemDTO, UpdateCartItemDTO
from src.core.exceptions import EntityNotFoundError, InsufficientStockError, ValidationError
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.core.interfaces.repositories.product_repository import IProductRepository


class GetCartUseCase:
    """Use case for retrieving the current customer's cart."""

    def __init__(
        self,
        cart_repository: ICartRepository,
        product_repository: IProductRepository,
    ) -> None:
        self.cart_repository = cart_repository
        self.product_repository = product_repository

    async def execute(
        self,
        store_id: UUID,
        customer_id: UUID,
        tenant_id: UUID,
    ) -> CartDTO:
        """Get or create the customer's cart with resolved product details."""
        cart = await self.cart_repository.get_or_create_cart(
            store_id=store_id,
            customer_id=customer_id,
            tenant_id=tenant_id,
        )

        # Batch-fetch all products in one query (avoids N+1)
        product_ids = [item.product_id for item in cart.items]
        products_map = await self.product_repository.get_by_ids(product_ids)

        subtotal = 0
        enriched_items: list[CartItemDTO] = []
        for item in cart.items:
            product = products_map.get(item.product_id)
            item_dto = CartItemDTO.from_entity(item)
            if product:
                item_dto.product_name = product.name
                item_dto.product_price = int(product.price.amount)
                item_dto.product_image = product.images[0] if product.images else None
                item_dto.in_stock = product.quantity >= item.quantity
                subtotal += int(product.price.amount) * item.quantity
            else:
                item_dto.in_stock = False
            enriched_items.append(item_dto)

        dto = CartDTO.from_entity(cart, subtotal=subtotal)
        dto.items = enriched_items
        return dto


class AddToCartUseCase:
    """Use case for adding an item to the cart."""

    def __init__(
        self,
        cart_repository: ICartRepository,
        product_repository: IProductRepository,
    ) -> None:
        self.cart_repository = cart_repository
        self.product_repository = product_repository

    async def execute(
        self,
        store_id: UUID,
        customer_id: UUID,
        tenant_id: UUID,
        dto: AddToCartDTO,
    ) -> CartDTO:
        """Add item to cart. Merges if same product+variant already exists."""
        # Validate product exists and belongs to this store
        product = await self.product_repository.get_by_id(dto.product_id)
        if not product:
            raise EntityNotFoundError("Product", str(dto.product_id))
        if product.store_id != store_id:
            raise EntityNotFoundError("Product", str(dto.product_id))

        # Validate stock availability
        cart = await self.cart_repository.get_or_create_cart(
            store_id=store_id,
            customer_id=customer_id,
            tenant_id=tenant_id,
        )

        # Check how many of this product are already in the cart
        existing = cart.find_item(dto.product_id, dto.variant_id)
        total_requested = dto.quantity + (existing.quantity if existing else 0)

        if product.quantity < total_requested:
            raise InsufficientStockError(
                product_name=product.name,
                available=product.quantity,
                requested=total_requested,
            )

        # Add or merge item
        cart.add_item(
            product_id=dto.product_id,
            quantity=dto.quantity,
            variant_id=dto.variant_id,
        )

        updated_cart = await self.cart_repository.update(cart)

        # Return enriched cart
        return await GetCartUseCase(
            self.cart_repository, self.product_repository
        ).execute(store_id, customer_id, tenant_id)


class UpdateCartItemUseCase:
    """Use case for updating cart item quantity."""

    def __init__(
        self,
        cart_repository: ICartRepository,
        product_repository: IProductRepository,
    ) -> None:
        self.cart_repository = cart_repository
        self.product_repository = product_repository

    async def execute(
        self,
        store_id: UUID,
        customer_id: UUID,
        tenant_id: UUID,
        item_id: UUID,
        dto: UpdateCartItemDTO,
    ) -> CartDTO:
        """Update quantity of a cart item."""
        cart = await self.cart_repository.get_active_cart(store_id, customer_id)
        if not cart:
            raise EntityNotFoundError("Cart")

        item = cart.find_item_by_id(item_id)
        if not item:
            raise EntityNotFoundError("CartItem", str(item_id))

        # Validate stock
        product = await self.product_repository.get_by_id(item.product_id)
        if not product:
            raise EntityNotFoundError("Product", str(item.product_id))

        if product.quantity < dto.quantity:
            raise InsufficientStockError(
                product_name=product.name,
                available=product.quantity,
                requested=dto.quantity,
            )

        cart.update_item(item_id, dto.quantity)
        await self.cart_repository.update(cart)

        return await GetCartUseCase(
            self.cart_repository, self.product_repository
        ).execute(store_id, customer_id, tenant_id)


class RemoveCartItemUseCase:
    """Use case for removing an item from the cart."""

    def __init__(
        self,
        cart_repository: ICartRepository,
        product_repository: IProductRepository,
    ) -> None:
        self.cart_repository = cart_repository
        self.product_repository = product_repository

    async def execute(
        self,
        store_id: UUID,
        customer_id: UUID,
        tenant_id: UUID,
        item_id: UUID,
    ) -> CartDTO:
        """Remove an item from the cart."""
        cart = await self.cart_repository.get_active_cart(store_id, customer_id)
        if not cart:
            raise EntityNotFoundError("Cart")

        cart.remove_item(item_id)
        await self.cart_repository.update(cart)

        return await GetCartUseCase(
            self.cart_repository, self.product_repository
        ).execute(store_id, customer_id, tenant_id)


class ClearCartUseCase:
    """Use case for clearing all items from the cart."""

    def __init__(self, cart_repository: ICartRepository) -> None:
        self.cart_repository = cart_repository

    async def execute(
        self,
        store_id: UUID,
        customer_id: UUID,
    ) -> None:
        """Clear all items from the cart."""
        cart = await self.cart_repository.get_active_cart(store_id, customer_id)
        if not cart:
            raise EntityNotFoundError("Cart")

        await self.cart_repository.clear_cart(cart.id)
