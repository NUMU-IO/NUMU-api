"""Create order use case."""

from uuid import UUID

from src.application.dto.order import CreateOrderDTO, OrderDTO
from src.config.logging_config import get_logger
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository

logger = get_logger(__name__)


class CreateOrderUseCase:
    """Use case for creating a new order."""

    def __init__(
        self,
        order_repository: IOrderRepository,
        store_repository: IStoreRepository,
        customer_repository: ICustomerRepository,
        onboarding_repository: IOnboardingRepository | None = None,
    ) -> None:
        self.order_repository = order_repository
        self.store_repository = store_repository
        self.customer_repository = customer_repository
        self.onboarding_repository = onboarding_repository

    async def execute(
        self,
        dto: CreateOrderDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> OrderDTO:
        """Create a new order."""
        log = logger.bind(
            store_id=str(store_id),
            user_id=str(user_id),
            customer_id=str(dto.customer_id),
        )
        log.info("order_create_attempt", item_count=len(dto.line_items))

        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            log.warning("order_create_failed", reason="store_not_found")
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            log.warning("order_create_failed", reason="unauthorized")
            raise AuthorizationError(
                "You don't have permission to create orders in this store"
            )

        # Verify customer exists
        customer = await self.customer_repository.get_by_id(dto.customer_id)
        if not customer:
            log.warning("order_create_failed", reason="customer_not_found")
            raise EntityNotFoundError("Customer", str(dto.customer_id))

        # Generate order number
        order_number = await self.order_repository.get_next_order_number(store_id)
        log = log.bind(order_number=order_number)

        # Convert line items
        line_items = []
        subtotal = 0
        for item_dto in dto.line_items:
            total_price = item_dto.unit_price * item_dto.quantity
            subtotal += total_price
            line_items.append(
                OrderLineItem(
                    product_id=item_dto.product_id,
                    product_name=item_dto.product_name,
                    variant_id=item_dto.variant_id,
                    variant_name=item_dto.variant_name,
                    sku=item_dto.sku,
                    quantity=item_dto.quantity,
                    unit_price=item_dto.unit_price,
                    total_price=total_price,
                )
            )

        # Convert shipping address
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

        # Convert billing address if provided
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

        # Calculate total
        total = subtotal + dto.shipping_cost + dto.tax_amount - dto.discount_amount

        # Create order entity
        order = Order(
            store_id=store_id,
            customer_id=dto.customer_id,
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

        # Save order
        created_order = await self.order_repository.create(order)

        log.info(
            "order_created",
            order_id=str(created_order.id),
            total=total,
            currency=dto.currency,
            payment_method=dto.payment_method,
        )

        # Auto-complete the first_order onboarding step
        if self.onboarding_repository:
            try:
                from src.application.use_cases.onboarding.auto_complete import (
                    try_complete_onboarding_step,
                )
                from src.core.entities.onboarding import OnboardingStepKey

                await try_complete_onboarding_step(
                    self.onboarding_repository,
                    store_id,
                    OnboardingStepKey.FIRST_ORDER,
                )
            except Exception:
                pass  # Never block order creation for onboarding

        return OrderDTO.from_entity(created_order)
