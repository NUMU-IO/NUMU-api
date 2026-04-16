"""Channel connection repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.channel_connection import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
)
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.infrastructure.database.models import ChannelConnectionModel


class ChannelConnectionRepositoryImpl(ChannelConnectionRepository):
    """Channel connection repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: ChannelConnectionModel) -> ChannelConnection:
        return ChannelConnection(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            channel=ChannelType(model.channel),
            status=ConnectionStatus(model.status),
            external_account_id=model.external_account_id,
            external_account_name=model.external_account_name,
            external_phone_number_id=model.external_phone_number_id,
            encrypted_credentials=model.encrypted_credentials,
            credential_key_id=model.credential_key_id,
            scopes=model.scopes or [],
            webhook_subscribed_at=model.webhook_subscribed_at,
            token_expires_at=model.token_expires_at,
            last_error=model.last_error,
            meta_business_id=model.meta_business_id,
            catalog_id=model.catalog_id,
            payment_configuration_id=model.payment_configuration_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> ChannelConnection | None:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(ChannelConnectionModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[ChannelConnection]:
        result = await self.session.execute(
            select(ChannelConnectionModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: ChannelConnection) -> ChannelConnection:
        model = ChannelConnectionModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            channel=entity.channel.value,
            status=entity.status.value,
            external_account_id=entity.external_account_id,
            external_account_name=entity.external_account_name,
            external_phone_number_id=entity.external_phone_number_id,
            encrypted_credentials=entity.encrypted_credentials,
            credential_key_id=entity.credential_key_id,
            scopes=entity.scopes,
            webhook_subscribed_at=entity.webhook_subscribed_at,
            token_expires_at=entity.token_expires_at,
            last_error=entity.last_error,
            meta_business_id=entity.meta_business_id,
            catalog_id=entity.catalog_id,
            payment_configuration_id=entity.payment_configuration_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return entity

    async def update(self, entity: ChannelConnection) -> ChannelConnection:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(ChannelConnectionModel.id == entity.id)
        )
        model = result.scalar_one()
        model.channel = entity.channel.value
        model.status = entity.status.value
        model.external_account_id = entity.external_account_id
        model.external_account_name = entity.external_account_name
        model.external_phone_number_id = entity.external_phone_number_id
        model.encrypted_credentials = entity.encrypted_credentials
        model.credential_key_id = entity.credential_key_id
        model.scopes = entity.scopes
        model.webhook_subscribed_at = entity.webhook_subscribed_at
        model.token_expires_at = entity.token_expires_at
        model.last_error = entity.last_error
        model.meta_business_id = entity.meta_business_id
        model.catalog_id = entity.catalog_id
        model.payment_configuration_id = entity.payment_configuration_id
        model.updated_at = entity.updated_at
        await self.session.flush()
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(ChannelConnectionModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(ChannelConnectionModel))
        return len(result.scalars().all())

    async def get_by_store_and_channel(
        self,
        store_id: UUID,
        channel: ChannelType,
    ) -> ChannelConnection | None:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.store_id == store_id,
                ChannelConnectionModel.channel == channel.value,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_store(
        self,
        store_id: UUID,
    ) -> list[ChannelConnection]:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.store_id == store_id,
            )
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_active_connections_by_store(
        self,
        store_id: UUID,
    ) -> list[ChannelConnection]:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.store_id == store_id,
                ChannelConnectionModel.status == ConnectionStatus.ACTIVE.value,
            )
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_external_account(
        self,
        store_id: UUID,
        channel: ChannelType,
        external_account_id: str,
    ) -> ChannelConnection | None:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.store_id == store_id,
                ChannelConnectionModel.channel == channel.value,
                ChannelConnectionModel.external_account_id == external_account_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def update_status(
        self,
        connection_id: UUID,
        status: ConnectionStatus,
        error: str | None = None,
    ) -> ChannelConnection | None:
        result = await self.session.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.id == connection_id
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.status = status.value
        model.last_error = error
        await self.session.flush()
        return self._to_entity(model)

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ChannelConnection]:
        result = await self.session.execute(
            select(ChannelConnectionModel)
            .where(ChannelConnectionModel.tenant_id == tenant_id)
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]
