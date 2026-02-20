"""Customer repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.customer import Customer
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.database.models import CustomerModel


class CustomerRepository(ICustomerRepository):
    """Customer repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: CustomerModel) -> Customer:
        """Convert database model to domain entity."""
        extra = model.extra_data or {}
        # Merge notification_prefs column into metadata
        if model.notification_prefs:
            extra["notification_preferences"] = model.notification_prefs
        return Customer(
            id=model.id,
            store_id=model.store_id,
            email=Email(value=model.email),
            first_name=model.first_name,
            last_name=model.last_name,
            phone=PhoneNumber(value=model.phone) if model.phone else None,
            password_hash=model.password_hash,
            user_id=model.user_id,
            accepts_marketing=model.accepts_marketing,
            is_verified=model.is_verified,
            notes=model.notes,
            tags=model.tags or [],
            default_address_id=model.default_address_id,
            total_orders=model.total_orders,
            total_spent=model.total_spent,
            metadata=extra,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Customer, tenant_id: UUID) -> CustomerModel:
        """Convert domain entity to database model."""
        meta = dict(entity.metadata)
        notif_prefs = meta.pop("notification_preferences", None)
        return CustomerModel(
            id=entity.id,
            tenant_id=tenant_id,
            store_id=entity.store_id,
            email=str(entity.email),
            first_name=entity.first_name,
            last_name=entity.last_name,
            phone=str(entity.phone) if entity.phone else None,
            password_hash=entity.password_hash,
            user_id=entity.user_id,
            accepts_marketing=entity.accepts_marketing,
            is_verified=entity.is_verified,
            notes=entity.notes,
            tags=entity.tags,
            default_address_id=entity.default_address_id,
            total_orders=entity.total_orders,
            total_spent=entity.total_spent,
            extra_data=meta,
            notification_prefs=notif_prefs
            or Customer.default_notification_preferences(),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Customer | None:
        """Get customer by ID."""
        result = await self.session.execute(
            select(CustomerModel).where(CustomerModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Customer]:
        """Get all customers with pagination."""
        result = await self.session.execute(
            select(CustomerModel).offset(skip).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Customer, tenant_id: UUID | None = None) -> Customer:
        """Create a new customer."""
        if tenant_id is None:
            raise ValueError("tenant_id is required for creating customer")
        model = self._to_model(entity, tenant_id)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Customer) -> Customer:
        """Update an existing customer."""
        result = await self.session.execute(
            select(CustomerModel).where(CustomerModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.email = str(entity.email)
            model.first_name = entity.first_name
            model.last_name = entity.last_name
            model.phone = str(entity.phone) if entity.phone else None
            model.password_hash = entity.password_hash
            model.accepts_marketing = entity.accepts_marketing
            model.is_verified = entity.is_verified
            model.notes = entity.notes
            model.tags = entity.tags
            model.default_address_id = entity.default_address_id
            model.total_orders = entity.total_orders
            model.total_spent = entity.total_spent
            meta = dict(entity.metadata)
            notif_prefs = meta.pop("notification_preferences", None)
            model.extra_data = meta
            if notif_prefs is not None:
                model.notification_prefs = notif_prefs
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Customer with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a customer by ID."""
        result = await self.session.execute(
            select(CustomerModel).where(CustomerModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of customers."""
        result = await self.session.execute(select(func.count(CustomerModel.id)))
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Customer]:
        """Get all customers for a store."""
        result = await self.session.execute(
            select(CustomerModel)
            .where(CustomerModel.store_id == store_id)
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_by_email(self, store_id: UUID, email: Email) -> Customer | None:
        """Get customer by email within a store."""
        result = await self.session.execute(
            select(CustomerModel).where(
                CustomerModel.store_id == store_id,
                CustomerModel.email == str(email).lower(),
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def email_exists(self, store_id: UUID, email: Email) -> bool:
        """Check if email already exists for a store."""
        result = await self.session.execute(
            select(CustomerModel.id).where(
                CustomerModel.store_id == store_id,
                CustomerModel.email == str(email).lower(),
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_by_user_id(self, store_id: UUID, user_id: UUID) -> Customer | None:
        """Get customer by user ID within a store."""
        result = await self.session.execute(
            select(CustomerModel).where(
                CustomerModel.store_id == store_id,
                CustomerModel.user_id == user_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def search(
        self,
        store_id: UUID,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Customer]:
        """Search customers by name or email."""
        search_pattern = f"%{query}%"
        result = await self.session.execute(
            select(CustomerModel)
            .where(
                CustomerModel.store_id == store_id,
                or_(
                    CustomerModel.email.ilike(search_pattern),
                    CustomerModel.first_name.ilike(search_pattern),
                    CustomerModel.last_name.ilike(search_pattern),
                ),
            )
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_top_customers(
        self,
        store_id: UUID,
        limit: int = 10,
    ) -> list[Customer]:
        """Get top customers by total spent."""
        result = await self.session.execute(
            select(CustomerModel)
            .where(CustomerModel.store_id == store_id)
            .order_by(CustomerModel.total_spent.desc())
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_by_store(
        self,
        store_id: UUID,
        date_from: datetime | None = None,
    ) -> int:
        """Get total count of customers for a store."""
        query = select(func.count(CustomerModel.id)).where(
            CustomerModel.store_id == store_id
        )
        if date_from:
            query = query.where(CustomerModel.created_at >= date_from)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def update_password(
        self, customer_id: UUID, password_hash: str
    ) -> Customer | None:
        """Update customer password hash."""
        result = await self.session.execute(
            select(CustomerModel).where(CustomerModel.id == customer_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.password_hash = password_hash
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        return None

    async def update_default_address(
        self, customer_id: UUID, address_id: UUID | None
    ) -> Customer | None:
        """Update customer's default address."""
        result = await self.session.execute(
            select(CustomerModel).where(CustomerModel.id == customer_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.default_address_id = address_id
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        return None

    async def verify_customer(self, customer_id: UUID) -> Customer | None:
        """Mark customer as verified."""
        result = await self.session.execute(
            select(CustomerModel).where(CustomerModel.id == customer_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.is_verified = True
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        return None
