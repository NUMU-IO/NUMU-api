"""Social connection repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.social_connection import (
    SocialConnection,
    SocialConnectionStatus,
    SocialPlatform,
)
from src.core.interfaces.repositories.social_connection_repository import (
    ISocialConnectionRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.social_connection import (
    SocialConnectionModel,
)


class SocialConnectionRepository(ISocialConnectionRepository):
    """Social connection repository using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(SocialConnectionModel.tenant_id == tid)
        return query

    def _to_entity(self, model: SocialConnectionModel) -> SocialConnection:
        return SocialConnection(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            platform=model.platform,
            platform_account_id=model.platform_account_id,
            handle=model.handle,
            followers=model.followers,
            posts_count=model.posts_count,
            access_token_encrypted=model.access_token_encrypted,
            token_expires_at=model.token_expires_at,
            status=model.status,
            last_synced_at=model.last_synced_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: SocialConnection) -> SocialConnectionModel:
        return SocialConnectionModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            platform=entity.platform,
            platform_account_id=entity.platform_account_id,
            handle=entity.handle,
            followers=entity.followers,
            posts_count=entity.posts_count,
            access_token_encrypted=entity.access_token_encrypted,
            token_expires_at=entity.token_expires_at,
            status=entity.status,
            last_synced_at=entity.last_synced_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> SocialConnection | None:
        query = select(SocialConnectionModel).where(
            SocialConnectionModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[SocialConnection]:
        query = select(SocialConnectionModel).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: SocialConnection) -> SocialConnection:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: SocialConnection) -> SocialConnection:
        query = select(SocialConnectionModel).where(
            SocialConnectionModel.id == entity.id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.platform = entity.platform
            model.platform_account_id = entity.platform_account_id
            model.handle = entity.handle
            model.followers = entity.followers
            model.posts_count = entity.posts_count
            model.access_token_encrypted = entity.access_token_encrypted
            model.token_expires_at = entity.token_expires_at
            model.status = entity.status
            model.last_synced_at = entity.last_synced_at
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"SocialConnection with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        query = select(SocialConnectionModel).where(
            SocialConnectionModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(
            select(func.count(SocialConnectionModel.id))
        )
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[SocialConnection]:
        query = (
            select(SocialConnectionModel)
            .where(
                SocialConnectionModel.store_id == store_id,
                SocialConnectionModel.status == SocialConnectionStatus.ACTIVE,
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_store_and_platform(
        self,
        store_id: UUID,
        platform: SocialPlatform,
    ) -> SocialConnection | None:
        query = select(SocialConnectionModel).where(
            SocialConnectionModel.store_id == store_id,
            SocialConnectionModel.platform == platform,
            SocialConnectionModel.status == SocialConnectionStatus.ACTIVE,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None
