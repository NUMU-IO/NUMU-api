"""Customer address repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.address import AddressLabel, CustomerAddress
from src.core.interfaces.repositories.address_repository import ICustomerAddressRepository
from src.infrastructure.database.models import CustomerAddressModel


class CustomerAddressRepository(ICustomerAddressRepository):
    """Customer address repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: CustomerAddressModel) -> CustomerAddress:
        """Convert database model to domain entity."""
        return CustomerAddress(
            id=model.id,
            customer_id=model.customer_id,
            first_name=model.first_name,
            last_name=model.last_name,
            address_line1=model.address_line1,
            address_line2=model.address_line2,
            city=model.city,
            state=model.state,
            postal_code=model.postal_code,
            country=model.country,
            phone=model.phone,
            is_default=model.is_default,
            label=model.label,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: CustomerAddress, tenant_id: UUID) -> CustomerAddressModel:
        """Convert domain entity to database model."""
        return CustomerAddressModel(
            id=entity.id,
            tenant_id=tenant_id,
            customer_id=entity.customer_id,
            first_name=entity.first_name,
            last_name=entity.last_name,
            address_line1=entity.address_line1,
            address_line2=entity.address_line2,
            city=entity.city,
            state=entity.state,
            postal_code=entity.postal_code,
            country=entity.country,
            phone=entity.phone,
            is_default=entity.is_default,
            label=entity.label,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> CustomerAddress | None:
        """Get address by ID."""
        result = await self.session.execute(
            select(CustomerAddressModel).where(CustomerAddressModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[CustomerAddress]:
        """Get all addresses with pagination."""
        result = await self.session.execute(
            select(CustomerAddressModel).offset(skip).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(
        self, entity: CustomerAddress, tenant_id: UUID | None = None
    ) -> CustomerAddress:
        """Create a new address."""
        if tenant_id is None:
            raise ValueError("tenant_id is required for creating address")
        model = self._to_model(entity, tenant_id)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: CustomerAddress) -> CustomerAddress:
        """Update an existing address."""
        result = await self.session.execute(
            select(CustomerAddressModel).where(CustomerAddressModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.first_name = entity.first_name
            model.last_name = entity.last_name
            model.address_line1 = entity.address_line1
            model.address_line2 = entity.address_line2
            model.city = entity.city
            model.state = entity.state
            model.postal_code = entity.postal_code
            model.country = entity.country
            model.phone = entity.phone
            model.is_default = entity.is_default
            model.label = entity.label
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Address with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete an address by ID."""
        result = await self.session.execute(
            select(CustomerAddressModel).where(CustomerAddressModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of addresses."""
        result = await self.session.execute(
            select(func.count(CustomerAddressModel.id))
        )
        return result.scalar() or 0

    async def get_by_customer(
        self,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CustomerAddress]:
        """Get all addresses for a customer."""
        result = await self.session.execute(
            select(CustomerAddressModel)
            .where(CustomerAddressModel.customer_id == customer_id)
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_default(self, customer_id: UUID) -> CustomerAddress | None:
        """Get default address for a customer."""
        result = await self.session.execute(
            select(CustomerAddressModel).where(
                CustomerAddressModel.customer_id == customer_id,
                CustomerAddressModel.is_default == True,  # noqa: E712
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def set_default(
        self, customer_id: UUID, address_id: UUID
    ) -> CustomerAddress | None:
        """Set an address as default, unsetting any previous default."""
        # First, unset all default addresses for this customer
        result = await self.session.execute(
            select(CustomerAddressModel).where(
                CustomerAddressModel.customer_id == customer_id,
                CustomerAddressModel.is_default == True,  # noqa: E712
            )
        )
        for model in result.scalars().all():
            model.is_default = False

        # Now set the new default
        result = await self.session.execute(
            select(CustomerAddressModel).where(CustomerAddressModel.id == address_id)
        )
        model = result.scalar_one_or_none()
        if model and model.customer_id == customer_id:
            model.is_default = True
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        return None

    async def count_by_customer(self, customer_id: UUID) -> int:
        """Get total count of addresses for a customer."""
        result = await self.session.execute(
            select(func.count(CustomerAddressModel.id)).where(
                CustomerAddressModel.customer_id == customer_id
            )
        )
        return result.scalar() or 0

    async def delete_by_customer(self, customer_id: UUID) -> int:
        """Delete all addresses for a customer. Returns count of deleted."""
        result = await self.session.execute(
            select(CustomerAddressModel).where(
                CustomerAddressModel.customer_id == customer_id
            )
        )
        models = result.scalars().all()
        count = len(models)
        for model in models:
            await self.session.delete(model)
        await self.session.flush()
        return count
