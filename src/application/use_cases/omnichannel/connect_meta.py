"""ConnectMeta use case - handles OAuth flow for connecting channels."""

from datetime import datetime
from uuid import UUID

from src.application.dto.omnichannel import ConnectMetaCallbackDTO
from src.core.entities.channel_connection import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
)
from src.core.events.base import EventBus
from src.core.exceptions import ValidationError
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.infrastructure.external_services.meta import MetaOAuthService
from src.infrastructure.external_services.secrets.secrets_manager import (
    SecretsManager,
)


class ConnectMetaUseCase:
    """Use case for connecting Meta channels via OAuth."""

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
        store_repository: IStoreRepository,
        oauth_service: MetaOAuthService | None = None,
        secrets_manager: SecretsManager | None = None,
        event_bus: EventBus | None = None,
    ):
        self.channel_connection_repository = channel_connection_repository
        self.store_repository = store_repository
        self.oauth_service = oauth_service or MetaOAuthService()
        self.secrets_manager = secrets_manager or SecretsManager()
        self.event_bus = event_bus

    async def start_oauth(
        self,
        store_id: UUID,
        redirect_uri: str,
    ) -> tuple[str, str]:
        """Start OAuth flow - return authorization URL and state.

        Args:
            store_id: The store UUID
            redirect_uri: OAuth callback URL

        Returns:
            Tuple of (authorization_url, state)
        """
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise ValidationError("Store not found")

        state = self.oauth_service.generate_state()

        auth_url = self.oauth_service.build_authorization_url(
            state=state,
            redirect_uri=redirect_uri,
        )

        return auth_url, state

    async def handle_callback(
        self,
        dto: ConnectMetaCallbackDTO,
        store_id: UUID,
    ) -> list[ChannelConnection]:
        """Handle OAuth callback - exchange code for tokens and save connection.

        Args:
            dto: OAuth callback data (code, state)
            store_id: The store UUID

        Returns:
            Created channel connections (facebook, instagram, whatsapp)
        """
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise ValidationError("Store not found")

        tokens = await self.oauth_service.exchange_code_for_tokens(
            code=dto.code,
            redirect_uri=dto.redirect_uri,
        )

        long_lived = await self.oauth_service.exchange_short_lived_for_long_lived(
            short_lived_token=tokens["access_token"],
        )

        access_token = long_lived["access_token"]
        pages = await self.oauth_service.get_pages(access_token)

        connections = []

        for page in pages:
            page_token = page.get("access_token")
            if not page_token:
                continue

            page_id = page["id"]
            page_name = page["name"]

            conn = await self._create_connection(
                store_id=store_id,
                channel=ChannelType.FACEBOOK,
                external_account_id=page_id,
                external_account_name=page_name,
                access_token=page_token,
                expires_at=long_lived.get("expires_at"),
                scopes=["pages_messaging", "pages_show_list"],
            )
            connections.append(conn)

            ig_account = await self.oauth_service.get_instagram_business_account(
                page_id=page_id,
                page_access_token=page_token,
            )
            if ig_account:
                ig_conn = await self._create_connection(
                    store_id=store_id,
                    channel=ChannelType.INSTAGRAM,
                    external_account_id=ig_account["id"],
                    external_account_name=ig_account.get("name", page_name),
                    access_token=page_token,
                    expires_at=long_lived.get("expires_at"),
                    scopes=["instagram_basic", "instagram_manage_messages"],
                )
                connections.append(ig_conn)

        waba_accounts = await self.oauth_service.get_whatsapp_business_accounts(
            access_token=access_token,
        )
        for waba in waba_accounts:
            waba_id = waba["id"]
            business_id = waba.get("business_id")

            phones = await self.oauth_service.get_whatsapp_phone_numbers(
                waba_id=waba_id,
                access_token=access_token,
            )

            for phone in phones:
                wa_conn = await self._create_connection(
                    store_id=store_id,
                    channel=ChannelType.WHATSAPP,
                    external_account_id=waba_id,
                    external_account_name=waba.get("business_name", "WhatsApp"),
                    access_token=access_token,
                    expires_at=long_lived.get("expires_at"),
                    external_phone_number_id=phone["id"],
                    scopes=[
                        "whatsapp_business_messaging",
                        "whatsapp_business_management",
                    ],
                    meta_business_id=business_id,
                )
                connections.append(wa_conn)

        return connections

    async def _create_connection(
        self,
        store_id: UUID,
        channel: ChannelType,
        external_account_id: str,
        external_account_name: str,
        access_token: str,
        expires_at: datetime | None,
        scopes: list[str],
        external_phone_number_id: str | None = None,
        meta_business_id: str | None = None,
    ) -> ChannelConnection:
        existing = await self.channel_connection_repository.get_by_external_account(
            store_id=store_id,
            channel=channel,
            external_account_id=external_account_id,
        )

        key_id = await self.secrets_manager.get_current_key_id()
        encrypted = await self.secrets_manager.encrypt(
            {"access_token": access_token}, key_id
        )

        if existing:
            existing.external_account_name = external_account_name
            existing.encrypted_credentials = encrypted
            existing.credential_key_id = key_id
            existing.scopes = scopes
            existing.token_expires_at = expires_at
            existing.status = ConnectionStatus.ACTIVE
            existing.external_phone_number_id = external_phone_number_id
            existing.meta_business_id = meta_business_id
            return await self.channel_connection_repository.update(existing)

        entity = ChannelConnection(
            tenant_id=store_id,
            store_id=store_id,
            channel=channel,
            status=ConnectionStatus.ACTIVE,
            external_account_id=external_account_id,
            external_account_name=external_account_name,
            external_phone_number_id=external_phone_number_id,
            encrypted_credentials=encrypted,
            credential_key_id=key_id,
            scopes=scopes,
            token_expires_at=expires_at,
            meta_business_id=meta_business_id,
        )
        return await self.channel_connection_repository.create(entity)
