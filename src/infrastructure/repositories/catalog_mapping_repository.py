"""Catalog mapping repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.catalog_mapping import CatalogMapping, CatalogSyncStatus
from src.core.interfaces.repositories.catalog_mapping_repository import (
    CatalogMappingRepository,
)
from src.infrastructure.database.models import CatalogMappingModel


class CatalogMappingRepositoryImpl(CatalogMappingRepository):
    """Catalog mapping repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: CatalogMappingModel) -> CatalogMapping:
        return CatalogMapping(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            product_id=model.product_id,
            channel_connection_id=model.channel_connection_id,
            external_catalog_id=model.external_catalog_id,
            external_product_id=model.external_product_id,
            sync_status=CatalogSyncStatus(model.sync_status),
            last_synced_at=model.last_synced_at,
            last_error=model.last_error,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> CatalogMapping | None:
        result = await self.session.execute(
            select(CatalogMappingModel).where(CatalogMappingModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[CatalogMapping]:
        result = await self.session.execute(
            select(CatalogMappingModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: CatalogMapping) -> CatalogMapping:
        model = CatalogMappingModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            product_id=entity.product_id,
            channel_connection_id=entity.channel_connection_id,
            external_catalog_id=entity.external_catalog_id,
            external_product_id=entity.external_product_id,
            sync_status=entity.sync_status.value,
            last_synced_at=entity.last_synced_at,
            last_error=entity.last_error,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return entity

    async def update(self, entity: CatalogMapping) -> CatalogMapping:
        result = await self.session.execute(
            select(CatalogMappingModel).where(CatalogMappingModel.id == entity.id)
        )
        model = result.scalar_one()
        model.external_catalog_id = entity.external_catalog_id
        model.external_product_id = entity.external_product_id
        model.sync_status = entity.sync_status.value
        model.last_synced_at = entity.last_synced_at
        model.last_error = entity.last_error
        model.updated_at = entity.updated_at
        await self.session.flush()
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(CatalogMappingModel).where(CatalogMappingModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(CatalogMappingModel))
        return len(result.scalars().all())

    async def get_by_product_and_connection(
        self,
        product_id: UUID,
        channel_connection_id: UUID,
    ) -> CatalogMapping | None:
        result = await self.session.execute(
            select(CatalogMappingModel).where(
                CatalogMappingModel.product_id == product_id,
                CatalogMappingModel.channel_connection_id == channel_connection_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_external_product(
        self,
        channel_connection_id: UUID,
        external_product_id: str,
    ) -> CatalogMapping | None:
        result = await self.session.execute(
            select(CatalogMappingModel).where(
                CatalogMappingModel.channel_connection_id == channel_connection_id,
                CatalogMappingModel.external_product_id == external_product_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_connection(
        self,
        channel_connection_id: UUID,
        sync_status: CatalogSyncStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CatalogMapping]:
        query = select(CatalogMappingModel).where(
            CatalogMappingModel.channel_connection_id == channel_connection_id
        )
        if sync_status:
            query = query.where(CatalogMappingModel.sync_status == sync_status.value)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_pending(self, limit: int = 100) -> list[CatalogMapping]:
        result = await self.session.execute(
            select(CatalogMappingModel)
            .where(CatalogMappingModel.sync_status == CatalogSyncStatus.PENDING.value)
            .limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update_sync_status(
        self,
        mapping_id: UUID,
        sync_status: CatalogSyncStatus,
        error: str | None = None,
    ) -> CatalogMapping | None:
        result = await self.session.execute(
            select(CatalogMappingModel).where(CatalogMappingModel.id == mapping_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.sync_status = sync_status.value
        model.last_error = error
        await self.session.flush()
        return self._to_entity(model)

    async def list_by_store(
        self,
        store_id: UUID,
        sync_status: CatalogSyncStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CatalogMapping]:
        query = select(CatalogMappingModel).where(
            CatalogMappingModel.store_id == store_id
        )
        if sync_status:
            query = query.where(CatalogMappingModel.sync_status == sync_status.value)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]
