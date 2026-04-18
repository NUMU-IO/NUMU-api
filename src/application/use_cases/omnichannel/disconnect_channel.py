"""Disconnect channel use case."""

from uuid import UUID

from src.core.entities.channel_connection import ConnectionStatus
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)


class DisconnectChannelUseCase:
    """Use case for disconnecting a channel.

    Contract: DELETE /stores/{store_id}/channels/connections/{connection_id}
    """

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
    ):
        self.channel_connection_repository = channel_connection_repository

    async def execute(
        self,
        connection_id: UUID,
        store_id: UUID,
    ) -> bool:
        """Disconnect and delete a channel connection.

        Args:
            connection_id: The connection UUID (from route path)
            store_id: Store UUID for ownership verification (from route path)

        Returns:
            True if disconnected

        Raises:
            ValidationError: If connection belongs to different store
        """
        connection = await self.channel_connection_repository.get_by_id(connection_id)
        if not connection:
            raise EntityNotFoundError("Channel connection not found")

        if connection.store_id != store_id:
            raise ValidationError("Connection does not belong to this store")

        await self.channel_connection_repository.update_status(
            connection_id=connection_id,
            status=ConnectionStatus.REVOKED,
        )

        return True
