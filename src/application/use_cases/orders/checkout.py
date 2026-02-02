"""Checkout use case - validates cart, checks stock, creates order, initiates Paymob payment."""

from dataclasses import dataclass, field
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.application.dto.order import OrderDTO
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
    FulfillmentStatus,
)
from src.core.exceptions import (
    EntityNotFoundError,
    PaymentError,
    ValidationError,
)
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.services.payment_service import IPaymentService


@dataclass
class CheckoutAddressDTO(BaseDTO):
    """Checkout shipping/billing address DTO."""

    first_name: str
    last_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    email: str | None = None


@dataclass
class CheckoutDTO(BaseDTO):
    """Checkout request DTO."""

    shipping_address: CheckoutAddressDTO
    billing_address: CheckoutAddressDTO | None = None
    customer_notes: str | None = None
    shipping_method: str | None = None
    shipping_cost: int = 0
    payment_method: str = "card"
    coupon_code: str | None = None  


@dataclass
class CheckoutResultDTO(BaseDTO):
    """Checkout result DTO."""

    success: bool
    order: OrderDTO | None = None
    payment_url: str | None = None  # Paymob iframe URL
    payment_key: str | None = None  # Payment key for SDK integration
    paymob_order_id: str | None = None
    error_message: str | None = None


class CheckoutUseCase:
    """Use case for processing checkout.

    Flow:
    1. Validate cart is not empty
    2. Validate all items are still in stock
    3. Create order from cart items
    4. Initiate Paymob payment and get iframe URL
    5. Clear cart on successful order creation
    """

    def __init__(
        self,
        cart_repository: ICartRepository,
        order_repository: IOrderRepository,
        product_repository: IProductRepository,
        customer_repository: ICustomerRepository,
        payment_service: IPaymentService,
        coupon_repository: ICouponRepository | None = None,
    ) -> None:
        """Initialize use case.

        Args:
            cart_repository: Cart repository instance.
            order_repository: Order repository instance.
            product_repository: Product repository instance.
            customer_repository: Customer repository instance.
            payment_service: Payment service (Paymob) instance.
            coupon_repository: Optional coupon repository instance.
        """
        self.cart_repository = cart_repository
        self.order_repository = order_repository
        self.product_repository = product_repository
        self.customer_repository = customer_repository
        self.payment_service = payment_service
        self.coupon_repository = coupon_repository

    async def execute(
        self,
        dto: CheckoutDTO,
        session_id: str,
        store_id: UUID,
        customer_id: UUID,
    ) -> CheckoutResultDTO:
        """Process checkout.

        Args:
            dto: Checkout data with addresses and shipping info.
            session_id: The session identifier.
            store_id: The store UUID.
            customer_id: The customer UUID (required for checkout).

        Returns:
            CheckoutResultDTO with order and payment URL.

        Raises:
            EntityNotFoundError: If cart, customer, or products not found.
            ValidationError: If cart is empty or stock is insufficient.
            PaymentError: If payment initiation fails.
        """
        
        cart = await self.cart_repository.get_by_customer_id(customer_id, store_id)
        if not cart:
            cart = await self.cart_repository.get_by_session_id(session_id, store_id)

        if not cart or cart.is_empty:
            raise ValidationError("Cart is empty. Add items before checkout.")

        # 2. Verify customer exists
        customer = await self.customer_repository.get_by_id(customer_id)
        if not customer:
            raise EntityNotFoundError("Customer", str(customer_id))

        # 3. Validate stock and prepare line items
        line_items: list[OrderLineItem] = []
        stock_errors: list[str] = []
        items_for_paymob: list[dict] = []

        for cart_item in cart.items:
            product = await self.product_repository.get_by_id(cart_item.product_id)

            if not product:
                stock_errors.append(
                    f"Product '{cart_item.product_name}' is no longer available"
                )
                continue

            if not product.is_in_stock:
                stock_errors.append(f"Product '{product.name}' is out of stock")
                continue

            if product.quantity < cart_item.quantity:
                stock_errors.append(
                    f"Insufficient stock for '{product.name}'. "
                    f"Available: {product.quantity}, Requested: {cart_item.quantity}"
                )
                continue

            # Get current price (may have changed since added to cart)
            current_price_cents = product.price.cents

            line_items.append(
                OrderLineItem(
                    product_id=cart_item.product_id,
                    product_name=cart_item.product_name,
                    variant_id=cart_item.variant_id,
                    variant_name=cart_item.variant_name,
                    sku=cart_item.sku,
                    quantity=cart_item.quantity,
                    unit_price=current_price_cents,
                    total_price=current_price_cents * cart_item.quantity,
                    weight=cart_item.weight,
                )
            )

            items_for_paymob.append(
                {
                    "name": cart_item.product_name,
                    "amount_cents": current_price_cents,
                    "quantity": cart_item.quantity,
                }
            )

        if stock_errors:
            raise ValidationError(
                "Stock validation failed:\n" + "\n".join(stock_errors)
            )

        # 4. Calculate totals
        subtotal = sum(item.total_price for item in line_items)
        shipping_cost = dto.shipping_cost
        tax_amount = 0  # TODO: Calculate tax based on address

        # 4b. Apply coupon if provided
        discount_amount = 0
        coupon_code = None
        coupon_id = None

        if dto.coupon_code and self.coupon_repository:
            from src.application.use_cases.coupons.apply_coupon import ApplyCouponUseCase

            apply_coupon = ApplyCouponUseCase(coupon_repository=self.coupon_repository)
            coupon_result = await apply_coupon.execute(
                store_id=store_id,
                coupon_code=dto.coupon_code,
                subtotal=subtotal,
                customer_id=customer_id,
            )
            discount_amount = coupon_result.calculated_discount
            coupon_code = coupon_result.coupon_code
            coupon_id = coupon_result.coupon_id

        total = subtotal + shipping_cost + tax_amount - discount_amount

        # 5. Generate order number
        order_number = await self.order_repository.get_next_order_number(store_id)

        # 6. Create shipping address
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

        # Create billing address (same as shipping if not provided)
        billing_address = shipping_address
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

        # 7. Create order
        order = Order(
            store_id=store_id,
            customer_id=customer_id,
            order_number=order_number,
            line_items=line_items,
            shipping_address=shipping_address,
            billing_address=billing_address,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            fulfillment_status=FulfillmentStatus.UNFULFILLED,
            subtotal=subtotal,
            shipping_cost=shipping_cost,
            tax_amount=tax_amount,
            discount_amount=discount_amount,
            coupon_code=coupon_code,
            coupon_id=coupon_id,
            total=total,
            currency=cart.currency,
            payment_method=dto.payment_method,
            shipping_method=dto.shipping_method,
            customer_notes=dto.customer_notes,
        )

        # Save order first to get ID
        order = await self.order_repository.create(order)

        # 7b. Increment coupon usage after order creation
        if coupon_id and self.coupon_repository:
            await self.coupon_repository.increment_usage(coupon_id)

        # 8. Initiate Paymob payment
        try:
            # Prepare billing data for Paymob
            billing_data = {
                "first_name": dto.shipping_address.first_name,
                "last_name": dto.shipping_address.last_name,
                "email": dto.shipping_address.email or customer.email or "customer@example.com",
                "phone_number": dto.shipping_address.phone or "+201000000000",
                "street": dto.shipping_address.address_line1,
                "building": dto.shipping_address.address_line2 or "NA",
                "floor": "NA",
                "apartment": "NA",
                "city": dto.shipping_address.city,
                "state": dto.shipping_address.state or "NA",
                "country": dto.shipping_address.country,
                "postal_code": dto.shipping_address.postal_code or "NA",
                "shipping_method": dto.shipping_method or "NA",
            }

            # Create payment intent
            payment_intent = await self.payment_service.create_payment_intent(
                amount=total,
                currency=cart.currency,
                customer_email=billing_data["email"],
                metadata={
                    "order_id": str(order.id),
                    "order_number": order_number,
                    "billing_data": billing_data,
                    "items": items_for_paymob,
                },
            )

            # Update order with payment info
            order.payment_id = payment_intent.id
            order = await self.order_repository.update(order)

            # 9. Clear cart after successful order creation
            cart.clear()
            await self.cart_repository.save(cart)

            # 10. Generate iframe URL
            iframe_url = None
            if hasattr(self.payment_service, "iframe_id") and self.payment_service.iframe_id:
                iframe_url = (
                    f"https://accept.paymob.com/api/acceptance/iframes/"
                    f"{self.payment_service.iframe_id}?payment_token={payment_intent.client_secret}"
                )

            return CheckoutResultDTO(
                success=True,
                order=OrderDTO.from_entity(order),
                payment_url=iframe_url,
                payment_key=payment_intent.client_secret,
                paymob_order_id=payment_intent.id,
            )

        except PaymentError as e:
            # Update order to failed status
            order.status = OrderStatus.PAYMENT_FAILED
            order.payment_status = PaymentStatus.FAILED
            await self.order_repository.update(order)

            return CheckoutResultDTO(
                success=False,
                order=OrderDTO.from_entity(order),
                error_message=str(e),
            )

        except Exception as e:
            # Update order to failed status
            order.status = OrderStatus.PAYMENT_FAILED
            order.payment_status = PaymentStatus.FAILED
            await self.order_repository.update(order)

            raise PaymentError(f"Failed to initiate payment: {str(e)}") from e
