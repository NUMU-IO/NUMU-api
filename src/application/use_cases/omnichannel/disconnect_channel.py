"""Disconnect channel use case."""

from uuid import UUID

from src.core.entities.channel_connection import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
)
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.core.logging import get_logger
from src.infrastructure.external_services.meta import MetaOAuthService
from src.infrastructure.external_services.secrets.secrets_manager import SecretsManager

logger = get_logger(__name__)


class DisconnectChannelUseCase:
    """Use case for disconnecting a channel.

    Contract: DELETE /stores/{store_id}/channels/connections/{connection_id}
    """

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
        oauth_service: MetaOAuthService | None = None,
        secrets_manager: SecretsManager | None = None,
    ):
        self.channel_connection_repository = channel_connection_repository
        self.oauth_service = oauth_service or MetaOAuthService()
        self.secrets_manager = secrets_manager or SecretsManager()

    async def execute(
        self,
        connection_id: UUID,
        store_id: UUID,
    ) -> bool:
        """Disconnect a channel connection.

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

        await self._detach_webhook(connection)

        await self.channel_connection_repository.update_status(
            connection_id=connection_id,
            status=ConnectionStatus.REVOKED,
        )

        return True

    async def _detach_webhook(self, connection: ChannelConnection) -> None:
        """Unsubscribe the Page at Meta so events stop being delivered.

        Best-effort: a failure here must not block the merchant from
        disconnecting, but it is logged loudly because the connection will
        keep receiving events until it is retried.

        Instagram rides on its linked Page's subscription, and that Page may
        still be connected on its own, so IG connections never unsubscribe —
        doing so would silently kill the Facebook inbox alongside it.
        """
        if connection.channel is not ChannelType.FACEBOOK:
            return
        page_id = connection.external_account_id
        if not page_id or not connection.encrypted_credentials:
            return

        try:
            credentials = await self.secrets_manager.decrypt(
                connection.encrypted_credentials,
                connection.credential_key_id,
            )
            token = credentials.get("access_token")
            if not token:
                return
            await self.oauth_service.unsubscribe_page_from_webhook(page_id, token)
        except Exception as exc:  # noqa: BLE001 - never block the disconnect
            logger.warning(
                "channel_disconnect_unsubscribe_failed",
                page_id=page_id,
                error=str(exc),
            )
