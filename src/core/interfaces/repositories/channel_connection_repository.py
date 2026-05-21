"""Channel connection repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.channel_connection import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
)
from src.core.interfaces.repositories.base import BaseRepository


class ChannelConnectionRepository(BaseRepository[ChannelConnection]):
    """Repository interface for channel connections."""

    @abstractmethod
    async def get_by_store_and_channel(
        self,
        store_id: UUID,
        channel: ChannelType,
    ) -> ChannelConnection | None:
        """Get a connection by store ID and channel type."""
        ...

    @abstractmethod
    async def get_active_connections_by_store(
        self,
        store_id: UUID,
    ) -> list[ChannelConnection]:
        """Get all active connections for a store."""
        ...

    @abstractmethod
    async def get_by_external_account(
        self,
        store_id: UUID,
        channel: ChannelType,
        external_account_id: str,
    ) -> ChannelConnection | None:
        """Get connection by external account ID."""
        ...

    @abstractmethod
    async def update_status(
        self,
        connection_id: UUID,
        status: ConnectionStatus,
        error: str | None = None,
    ) -> ChannelConnection | None:
        """Update connection status."""
        ...

    @abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ChannelConnection]:
        """List all connections for a tenant."""
        ...
