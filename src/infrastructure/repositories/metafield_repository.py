"""Metafield repository implementations (definitions + values)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.metafield import (
    MetafieldDefinition,
    MetafieldOwnerType,
    MetafieldType,
    MetafieldValue,
    ResolvedMetafield,
    coerce_metafield_value,
)
from src.core.interfaces.repositories.metafield_repository import (
    IMetafieldDefinitionRepository,
    IMetafieldValueRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.metafield import (
    MetafieldDefinitionModel,
    MetafieldValueModel,
)


class MetafieldDefinitionRepository(IMetafieldDefinitionRepository):
    """SQLAlchemy repository for metafield definitions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(MetafieldDefinitionModel.tenant_id == tid)
        return query

    def _to_entity(self, model: MetafieldDefinitionModel) -> MetafieldDefinition:
        return MetafieldDefinition(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            owner_type=MetafieldOwnerType(model.owner_type),
            namespace=model.namespace,
            key=model.key,
            type=MetafieldType(model.type),
            name=model.name,
            description=model.description,
            is_public=model.is_public,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: MetafieldDefinition) -> MetafieldDefinitionModel:
        return MetafieldDefinitionModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            owner_type=MetafieldOwnerType(entity.owner_type).value,
            namespace=entity.namespace,
            key=entity.key,
            type=MetafieldType(entity.type).value,
            name=entity.name,
            description=entity.description,
            is_public=entity.is_public,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> MetafieldDefinition | None:
        query = select(MetafieldDefinitionModel).where(
            MetafieldDefinitionModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self, skip: int = 0, limit: int = 100
    ) -> list[MetafieldDefinition]:
        query = (
            select(MetafieldDefinitionModel)
            .order_by(
                MetafieldDefinitionModel.namespace,
                MetafieldDefinitionModel.key,
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: MetafieldDefinition) -> MetafieldDefinition:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: MetafieldDefinition) -> MetafieldDefinition:
        query = select(MetafieldDefinitionModel).where(
            MetafieldDefinitionModel.id == entity.id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Metafield definition {entity.id} not found")
        # namespace/key/owner_type are the immutable address; only display
        # metadata, type and visibility are updatable.
        model.type = MetafieldType(entity.type).value
        model.name = entity.name
        model.description = entity.description
        model.is_public = entity.is_public
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        query = select(MetafieldDefinitionModel).where(
            MetafieldDefinitionModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def count(self) -> int:
        result = await self.session.execute(
            self._tenant_filter(select(func.count(MetafieldDefinitionModel.id)))
        )
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        owner_type: MetafieldOwnerType | None = None,
    ) -> list[MetafieldDefinition]:
        query = select(MetafieldDefinitionModel).where(
            MetafieldDefinitionModel.store_id == store_id
        )
        if owner_type is not None:
            query = query.where(
                MetafieldDefinitionModel.owner_type
                == MetafieldOwnerType(owner_type).value
            )
        query = query.order_by(
            MetafieldDefinitionModel.owner_type,
            MetafieldDefinitionModel.namespace,
            MetafieldDefinitionModel.key,
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_key(
        self,
        store_id: UUID,
        owner_type: MetafieldOwnerType,
        namespace: str,
        key: str,
    ) -> MetafieldDefinition | None:
        query = select(MetafieldDefinitionModel).where(
            MetafieldDefinitionModel.store_id == store_id,
            MetafieldDefinitionModel.owner_type == MetafieldOwnerType(owner_type).value,
            MetafieldDefinitionModel.namespace == namespace,
            MetafieldDefinitionModel.key == key,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None


class MetafieldValueRepository(IMetafieldValueRepository):
    """SQLAlchemy repository for metafield values."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(MetafieldValueModel.tenant_id == tid)
        return query

    def _to_entity(self, model: MetafieldValueModel) -> MetafieldValue:
        return MetafieldValue(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            definition_id=model.definition_id,
            owner_id=model.owner_id,
            value=model.value,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: MetafieldValue) -> MetafieldValueModel:
        return MetafieldValueModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            definition_id=entity.definition_id,
            owner_id=entity.owner_id,
            value=entity.value,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> MetafieldValue | None:
        query = select(MetafieldValueModel).where(MetafieldValueModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[MetafieldValue]:
        query = select(MetafieldValueModel).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: MetafieldValue) -> MetafieldValue:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: MetafieldValue) -> MetafieldValue:
        query = select(MetafieldValueModel).where(MetafieldValueModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Metafield value {entity.id} not found")
        model.value = entity.value
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        query = select(MetafieldValueModel).where(MetafieldValueModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def count(self) -> int:
        result = await self.session.execute(
            self._tenant_filter(select(func.count(MetafieldValueModel.id)))
        )
        return result.scalar() or 0

    async def get_for_owner(
        self, store_id: UUID, owner_id: UUID
    ) -> list[MetafieldValue]:
        query = select(MetafieldValueModel).where(
            MetafieldValueModel.store_id == store_id,
            MetafieldValueModel.owner_id == owner_id,
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_definition_and_owner(
        self, definition_id: UUID, owner_id: UUID
    ) -> MetafieldValue | None:
        query = select(MetafieldValueModel).where(
            MetafieldValueModel.definition_id == definition_id,
            MetafieldValueModel.owner_id == owner_id,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_public_for_owner(
        self,
        store_id: UUID,
        owner_type: MetafieldOwnerType,
        owner_id: UUID,
    ) -> list[ResolvedMetafield]:
        """Join values → definitions, keep only PUBLIC ones, type the value.

        Scoped by ``store_id`` on both sides so a value can never resolve
        against another store's definition. Returns theme-ready records with
        the value already coerced to its declared Python type.
        """
        query = (
            select(MetafieldDefinitionModel, MetafieldValueModel)
            .join(
                MetafieldValueModel,
                MetafieldValueModel.definition_id == MetafieldDefinitionModel.id,
            )
            .where(
                MetafieldDefinitionModel.store_id == store_id,
                MetafieldValueModel.store_id == store_id,
                MetafieldDefinitionModel.owner_type
                == MetafieldOwnerType(owner_type).value,
                MetafieldValueModel.owner_id == owner_id,
                MetafieldDefinitionModel.is_public.is_(True),
            )
            .order_by(
                MetafieldDefinitionModel.namespace,
                MetafieldDefinitionModel.key,
            )
        )
        result = await self.session.execute(query)
        resolved: list[ResolvedMetafield] = []
        for definition, value in result.all():
            mtype = MetafieldType(definition.type)
            resolved.append(
                ResolvedMetafield(
                    namespace=definition.namespace,
                    key=definition.key,
                    type=mtype,
                    value=coerce_metafield_value(mtype, value.value),
                )
            )
        return resolved
