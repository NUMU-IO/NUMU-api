"""Order repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.order import (
    FulfillmentStatus,
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.infrastructure.database.models import OrderModel


class OrderRepository(IOrderRepository):
    """Order repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _line_item_to_dict(self, item: OrderLineItem) -> dict:
        """Convert OrderLineItem to dict for storage."""
        return {
            "product_id": str(item.product_id),
            "product_name": item.product_name,
            "variant_id": str(item.variant_id) if item.variant_id else None,
            "variant_name": item.variant_name,
            "sku": item.sku,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "total_price": item.total_price,
            "weight": str(item.weight) if item.weight else None,
            "properties": item.properties,
        }

    def _dict_to_line_item(self, data: dict) -> OrderLineItem:
        """Convert dict to OrderLineItem."""
        from decimal import Decimal
        return OrderLineItem(
            product_id=UUID(data["product_id"]),
            product_name=data["product_name"],
            variant_id=UUID(data["variant_id"]) if data.get("variant_id") else None,
            variant_name=data.get("variant_name"),
            sku=data.get("sku"),
            quantity=data.get("quantity", 1),
            unit_price=data.get("unit_price", 0),
            total_price=data.get("total_price", 0),
            weight=Decimal(data["weight"]) if data.get("weight") else None,
            properties=data.get("properties", {}),
        )

    def _address_to_dict(self, address: OrderShippingAddress) -> dict:
        """Convert OrderShippingAddress to dict for storage."""
        return {
            "first_name": address.first_name,
            "last_name": address.last_name,
            "address_line1": address.address_line1,
            "address_line2": address.address_line2,
            "city": address.city,
            "state": address.state,
            "postal_code": address.postal_code,
            "country": address.country,
            "phone": address.phone,
        }

    def _dict_to_address(self, data: dict) -> OrderShippingAddress:
        """Convert dict to OrderShippingAddress."""
        return OrderShippingAddress(
            first_name=data["first_name"],
            last_name=data["last_name"],
            address_line1=data["address_line1"],
            address_line2=data.get("address_line2"),
            city=data["city"],
            state=data.get("state"),
            postal_code=data.get("postal_code"),
            country=data["country"],
            phone=data.get("phone"),
        )

    def _to_entity(self, model: OrderModel) -> Order:
        """Convert database model to domain entity."""
        return Order(
            id=model.id,
            store_id=model.store_id,
            customer_id=model.customer_id,
            order_number=model.order_number,
            line_items=[self._dict_to_line_item(item) for item in (model.line_items or [])],
            shipping_address=self._dict_to_address(model.shipping_address),
            billing_address=self._dict_to_address(model.billing_address) if model.billing_address else None,
            status=model.status,
            payment_status=model.payment_status,
            fulfillment_status=model.fulfillment_status,
            subtotal=model.subtotal,
            shipping_cost=model.shipping_cost,
            tax_amount=model.tax_amount,
            discount_amount=model.discount_amount,
            total=model.total,
            currency=model.currency,
            payment_method=model.payment_method,
            payment_id=model.payment_id,
            shipping_method=model.shipping_method,
            tracking_number=model.tracking_number,
            notes=model.notes,
            customer_notes=model.customer_notes,
            metadata=model.extra_data or {},
            cancelled_at=model.cancelled_at,
            paid_at=model.paid_at,
            fulfilled_at=model.fulfilled_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Order) -> OrderModel:
        """Convert domain entity to database model."""
        return OrderModel(
            id=entity.id,
            store_id=entity.store_id,
            customer_id=entity.customer_id,
            order_number=entity.order_number,
            line_items=[self._line_item_to_dict(item) for item in entity.line_items],
            shipping_address=self._address_to_dict(entity.shipping_address),
            billing_address=self._address_to_dict(entity.billing_address) if entity.billing_address else None,
            status=entity.status,
            payment_status=entity.payment_status,
            fulfillment_status=entity.fulfillment_status,
            subtotal=entity.subtotal,
            shipping_cost=entity.shipping_cost,
            tax_amount=entity.tax_amount,
            discount_amount=entity.discount_amount,
            total=entity.total,
            currency=entity.currency,
            payment_method=entity.payment_method,
            payment_id=entity.payment_id,
            shipping_method=entity.shipping_method,
            tracking_number=entity.tracking_number,
            notes=entity.notes,
            customer_notes=entity.customer_notes,
            extra_data=entity.metadata,
            cancelled_at=entity.cancelled_at,
            paid_at=entity.paid_at,
            fulfilled_at=entity.fulfilled_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Order | None:
        """Get order by ID."""
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """Get all orders with pagination."""
        result = await self.session.execute(
            select(OrderModel)
            .order_by(OrderModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Order) -> Order:
        """Create a new order."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Order) -> Order:
        """Update an existing order."""
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.status = entity.status
            model.payment_status = entity.payment_status
            model.fulfillment_status = entity.fulfillment_status
            model.line_items = [self._line_item_to_dict(item) for item in entity.line_items]
            model.shipping_address = self._address_to_dict(entity.shipping_address)
            model.billing_address = self._address_to_dict(entity.billing_address) if entity.billing_address else None
            model.subtotal = entity.subtotal
            model.shipping_cost = entity.shipping_cost
            model.tax_amount = entity.tax_amount
            model.discount_amount = entity.discount_amount
            model.total = entity.total
            model.currency = entity.currency
            model.payment_method = entity.payment_method
            model.payment_id = entity.payment_id
            model.shipping_method = entity.shipping_method
            model.tracking_number = entity.tracking_number
            model.notes = entity.notes
            model.customer_notes = entity.customer_notes
            model.extra_data = entity.metadata
            model.cancelled_at = entity.cancelled_at
            model.paid_at = entity.paid_at
            model.fulfilled_at = entity.fulfilled_at
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Order with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete an order by ID."""
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of orders."""
        result = await self.session.execute(
            select(func.count(OrderModel.id))
        )
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: OrderStatus | None = None,
        payment_status: PaymentStatus | None = None,
        fulfillment_status: FulfillmentStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[Order]:
        """Get all orders for a store with optional filters."""
        query = select(OrderModel).where(OrderModel.store_id == store_id)
        if status:
            query = query.where(OrderModel.status == status)
        if payment_status:
            query = query.where(OrderModel.payment_status == payment_status)
        if fulfillment_status:
            query = query.where(OrderModel.fulfillment_status == fulfillment_status)
        if date_from:
            query = query.where(OrderModel.created_at >= date_from)
        if date_to:
            query = query.where(OrderModel.created_at <= date_to)
        query = query.order_by(OrderModel.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_by_customer(
        self,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """Get all orders for a customer."""
        result = await self.session.execute(
            select(OrderModel)
            .where(OrderModel.customer_id == customer_id)
            .order_by(OrderModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_by_order_number(
        self,
        store_id: UUID,
        order_number: str,
    ) -> Order | None:
        """Get order by order number within a store."""
        result = await self.session.execute(
            select(OrderModel).where(
                OrderModel.store_id == store_id,
                OrderModel.order_number == order_number,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_payment_id(self, payment_id: str) -> Order | None:
        """Get order by external payment ID."""
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.payment_id == payment_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_date_range(
        self,
        store_id: UUID,
        start_date: datetime,
        end_date: datetime,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """Get orders within a date range."""
        result = await self.session.execute(
            select(OrderModel)
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= start_date,
                OrderModel.created_at <= end_date,
            )
            .order_by(OrderModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_by_store(
        self,
        store_id: UUID,
        status: OrderStatus | None = None,
        payment_status: PaymentStatus | None = None,
        fulfillment_status: FulfillmentStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        """Get total count of orders for a store with optional filters."""
        query = select(func.count(OrderModel.id)).where(OrderModel.store_id == store_id)
        if status:
            query = query.where(OrderModel.status == status)
        if payment_status:
            query = query.where(OrderModel.payment_status == payment_status)
        if fulfillment_status:
            query = query.where(OrderModel.fulfillment_status == fulfillment_status)
        if date_from:
            query = query.where(OrderModel.created_at >= date_from)
        if date_to:
            query = query.where(OrderModel.created_at <= date_to)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def count_by_customer(self, customer_id: UUID) -> int:
        """Get total count of orders for a customer."""
        result = await self.session.execute(
            select(func.count(OrderModel.id)).where(OrderModel.customer_id == customer_id)
        )
        return result.scalar() or 0

    async def get_revenue_by_date_range(
        self,
        store_id: UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> int:
        """Get total revenue for a date range (in cents)."""
        result = await self.session.execute(
            select(func.coalesce(func.sum(OrderModel.total), 0))
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= start_date,
                OrderModel.created_at <= end_date,
                OrderModel.payment_status == PaymentStatus.PAID,
            )
        )
        return result.scalar() or 0

    async def get_next_order_number(self, store_id: UUID) -> str:
        """Generate next order number for a store."""
        result = await self.session.execute(
            select(func.count(OrderModel.id)).where(OrderModel.store_id == store_id)
        )
        count = result.scalar() or 0
        return f"ORD-{count + 1:06d}"

    async def search(
        self,
        store_id: UUID,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """Search orders by order number or customer notes."""
        from sqlalchemy import or_
        search_term = f"%{query}%"
        result = await self.session.execute(
            select(OrderModel)
            .where(
                OrderModel.store_id == store_id,
                or_(
                    OrderModel.order_number.ilike(search_term),
                    OrderModel.customer_notes.ilike(search_term),
                ),
            )
            .order_by(OrderModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]
