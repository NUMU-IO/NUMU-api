"""Use case: Disconnect a social media account."""

from uuid import UUID

from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.social_connection_repository import (
    ISocialConnectionRepository,
)


class DisconnectSocialAccountUseCase:
    """Disconnect a social media account and revoke the stored token."""

    def __init__(
        self,
        connection_repo: ISocialConnectionRepository,
    ) -> None:
        self.connection_repo = connection_repo

    async def execute(self, connection_id: UUID) -> None:
        """Disconnect the social connection."""
        connection = await self.connection_repo.get_by_id(connection_id)
        if not connection:
            raise EntityNotFoundError("SocialConnection", str(connection_id))

        connection.disconnect()
        await self.connection_repo.update(connection)
