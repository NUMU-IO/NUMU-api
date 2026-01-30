"""Cart repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.entities.cart import Cart, CartItem
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.infrastructure.database.models.tenant.cart import CartItemModel, CartModel


class CartRepository(ICartRepository):
    """Cart repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _item_to_entity(self, model: CartItemModel) -> CartItem:
        """Convert CartItemModel to CartItem entity."""
        return CartItem(
            id=model.id,
            cart_id=model.cart_id,
            product_id=model.product_id,
            quantity=model.quantity,
            variant_id=model.variant_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_entity(self, model: CartModel) -> Cart:
        """Convert CartModel to Cart entity."""
        return Cart(
            id=model.id,
            store_id=model.store_id,
            customer_id=model.customer_id,
            items=[self._item_to_entity(item) for item in (model.items or [])],
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Cart, tenant_id: UUID) -> CartModel:
        """Convert Cart entity to CartModel."""
        return CartModel(
            id=entity.id,
            store_id=entity.store_id,
            customer_id=entity.customer_id,
            tenant_id=tenant_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Cart | None:
        """Get cart by ID."""
        result = await self.session.execute(
            select(CartModel)
            .options(selectinload(CartModel.items))
            .where(CartModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Cart]:
        """Get all carts with pagination."""
        result = await self.session.execute(
            select(CartModel)
            .options(selectinload(CartModel.items))
            .order_by(CartModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Cart) -> Cart:
        """Create a new cart."""
        raise NotImplementedError("Use get_or_create_cart instead")

    async def update(self, entity: Cart) -> Cart:
        """Update an existing cart."""
        result = await self.session.execute(
            select(CartModel)
            .options(selectinload(CartModel.items))
            .where(CartModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Cart with id {entity.id} not found")

        # Sync items: build a map of existing DB items by ID
        existing_items = {item.id: item for item in model.items}
        entity_item_ids = {item.id for item in entity.items}

        # Remove items no longer in entity
        for item_model in list(model.items):
            if item_model.id not in entity_item_ids:
                await self.session.delete(item_model)

        # Add or update items
        for item_entity in entity.items:
            if item_entity.id in existing_items:
                # Update existing
                item_model = existing_items[item_entity.id]
                item_model.quantity = item_entity.quantity
                item_model.variant_id = item_entity.variant_id
            else:
                # Add new
                new_item = CartItemModel(
                    id=item_entity.id,
                    cart_id=model.id,
                    product_id=item_entity.product_id,
                    quantity=item_entity.quantity,
                    variant_id=item_entity.variant_id,
                    tenant_id=model.tenant_id,
                )
                self.session.add(new_item)

        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a cart by ID."""
        result = await self.session.execute(
            select(CartModel).where(CartModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of carts."""
        result = await self.session.execute(
            select(func.count(CartModel.id))
        )
        return result.scalar() or 0

    async def get_active_cart(
        self,
        store_id: UUID,
        customer_id: UUID,
    ) -> Cart | None:
        """Get the active cart for a customer in a store."""
        result = await self.session.execute(
            select(CartModel)
            .options(selectinload(CartModel.items))
            .where(
                CartModel.store_id == store_id,
                CartModel.customer_id == customer_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_or_create_cart(
        self,
        store_id: UUID,
        customer_id: UUID,
        tenant_id: UUID,
    ) -> Cart:
        """Get existing cart or create a new one."""
        cart = await self.get_active_cart(store_id, customer_id)
        if cart:
            return cart

        # Create new cart
        cart_entity = Cart(store_id=store_id, customer_id=customer_id)
        model = self._to_model(cart_entity, tenant_id)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def clear_cart(self, cart_id: UUID) -> None:
        """Remove all items from a cart."""
        result = await self.session.execute(
            select(CartItemModel).where(CartItemModel.cart_id == cart_id)
        )
        items = result.scalars().all()
        for item in items:
            await self.session.delete(item)
        await self.session.flush()
