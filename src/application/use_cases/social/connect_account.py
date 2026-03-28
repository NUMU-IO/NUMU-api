"""Use case: Connect a social media account."""

import logging
from uuid import UUID

from src.core.entities.social_connection import (
    SocialConnection,
    SocialConnectionStatus,
    SocialPlatform,
)
from src.core.interfaces.repositories.social_connection_repository import (
    ISocialConnectionRepository,
)
from src.infrastructure.external_services.meta import MetaSocialService

logger = logging.getLogger(__name__)


class ConnectSocialAccountUseCase:
    """Initiate or complete an OAuth connection for a social platform."""

    def __init__(
        self,
        connection_repo: ISocialConnectionRepository,
        meta_service: MetaSocialService,
    ) -> None:
        self.connection_repo = connection_repo
        self.meta_service = meta_service

    def get_auth_url(self, platform: SocialPlatform, redirect_uri: str) -> str:
        """Return the OAuth URL the frontend should redirect the merchant to."""
        return self.meta_service.get_auth_url(platform, redirect_uri)

    async def complete_connection(
        self,
        store_id: UUID,
        tenant_id: UUID,
        platform: SocialPlatform,
        oauth_code: str,
    ) -> SocialConnection:
        """Exchange the OAuth code and store the connection.

        If a connection for this store+platform already exists and is active,
        update it instead of creating a duplicate.
        """
        # Check for existing connection
        existing = await self.connection_repo.get_by_store_and_platform(
            store_id, platform
        )
        if existing and existing.is_active:
            return existing

        # Exchange code for token
        raw_token = await self.meta_service.exchange_token(platform, oauth_code)
        account_info = await self.meta_service.get_account_info(platform, raw_token)

        # Encrypt the token before storage
        encrypted_token = raw_token
        try:
            from src.infrastructure.external_services.secrets import (
                get_secrets_manager,
            )

            secrets_mgr = get_secrets_manager()
            encrypted_token = secrets_mgr.encrypt(raw_token)
        except Exception:
            logger.warning(
                "SecretsManager not configured — storing token unencrypted (dev only)"
            )

        connection = SocialConnection(
            store_id=store_id,
            tenant_id=tenant_id,
            platform=platform,
            platform_account_id=account_info.platform_account_id,
            handle=account_info.handle,
            followers=account_info.followers,
            posts_count=account_info.posts_count,
            access_token_encrypted=encrypted_token,
            status=SocialConnectionStatus.ACTIVE,
        )

        return await self.connection_repo.create(connection)
