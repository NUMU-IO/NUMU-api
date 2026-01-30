"""Checkout use case for storefront."""

from uuid import UUID

from src.application.dto.checkout import CheckoutDTO
from src.application.dto.order import OrderDTO
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.exceptions import EntityNotFoundError, InsufficientStockError, ValidationError
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class CheckoutUseCase:
    """Use case for converting a cart into an order.

    Atomic operation: validates stock, creates order, deducts inventory,
    and clears cart within a single database transaction.
    """

    def __init__(
        self,
        cart_repository: ICartRepository,
        order_repository: IOrderRepository,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.cart_repository = cart_repository
        self.order_repository = order_repository
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        customer_id: UUID,
        dto: CheckoutDTO,
    ) -> OrderDTO:
        """Convert the customer's cart into an order.

        Steps (all within one transaction):
        1. Validate cart exists and is not empty
        2. Resolve and validate all product prices and stock
        3. Build order line items with current prices
        4. Create the order
        5. Deduct inventory
        6. Clear the cart
        """
        # 1. Get the customer's cart
        cart = await self.cart_repository.get_active_cart(store_id, customer_id)
        if not cart or cart.is_empty:
            raise ValidationError("Cart is empty. Add items before checkout.")

        # Verify store exists
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        # 2. Resolve products and validate stock at checkout time
        line_items: list[OrderLineItem] = []
        inventory_updates: list[tuple[UUID, int]] = []
        subtotal = 0

        for cart_item in cart.items:
            product = await self.product_repository.get_by_id(cart_item.product_id)
            if not product:
                raise EntityNotFoundError("Product", str(cart_item.product_id))

            if product.store_id != store_id:
                raise ValidationError(
                    f"Product '{product.name}' does not belong to this store."
                )

            # Validate stock at checkout (double-check)
            if product.quantity < cart_item.quantity:
                raise InsufficientStockError(
                    product_name=product.name,
                    available=product.quantity,
                    requested=cart_item.quantity,
                )

            # Build line item with current price (cast Decimal to int cents)
            unit_price = int(product.price.amount)
            total_price = unit_price * cart_item.quantity
            subtotal += total_price

            line_items.append(
                OrderLineItem(
                    product_id=product.id,
                    product_name=product.name,
                    variant_id=cart_item.variant_id,
                    sku=product.sku,
                    quantity=cart_item.quantity,
                    unit_price=unit_price,
                    total_price=total_price,
                    weight=product.weight,
                )
            )

            # Track inventory deduction (negative delta)
            inventory_updates.append((product.id, -cart_item.quantity))

        # 3. Build shipping address
        shipping_address = OrderShippingAddress(
            first_name=dto.shipping_address.first_name,
            last_name=dto.shipping_address.last_name,
            address_line1=dto.shipping_address.address_line1,
            address_line2=dto.shipping_address.address_line2,
            city=dto.shipping_address.city,
            state=dto.shipping_address.state,
            postal_code=dto.shipping_address.postal_code,
            country=dto.shipping_address.country,
            phone=dto.shipping_address.phone,
        )

        billing_address = None
        if dto.billing_address:
            billing_address = OrderShippingAddress(
                first_name=dto.billing_address.first_name,
                last_name=dto.billing_address.last_name,
                address_line1=dto.billing_address.address_line1,
                address_line2=dto.billing_address.address_line2,
                city=dto.billing_address.city,
                state=dto.billing_address.state,
                postal_code=dto.billing_address.postal_code,
                country=dto.billing_address.country,
                phone=dto.billing_address.phone,
            )

        # 4. Calculate total
        total = subtotal + dto.shipping_cost + dto.tax_amount - dto.discount_amount

        # Generate order number
        order_number = await self.order_repository.get_next_order_number(store_id)

        # 5. Create order entity
        order = Order(
            store_id=store_id,
            customer_id=customer_id,
            order_number=order_number,
            line_items=line_items,
            shipping_address=shipping_address,
            billing_address=billing_address,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            subtotal=subtotal,
            shipping_cost=dto.shipping_cost,
            tax_amount=dto.tax_amount,
            discount_amount=dto.discount_amount,
            total=total,
            currency=dto.currency,
            payment_method=dto.payment_method,
            shipping_method=dto.shipping_method,
            customer_notes=dto.customer_notes,
        )

        created_order = await self.order_repository.create(order)

        # 6. Deduct inventory
        await self.product_repository.bulk_update_quantity(inventory_updates)

        # 7. Clear the cart
        await self.cart_repository.clear_cart(cart.id)

        return OrderDTO.from_entity(created_order)
