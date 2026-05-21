"""Template use cases."""

from uuid import UUID

from src.application.dto.omnichannel import WhatsAppTemplateDTO
from src.core.entities.whatsapp_template import (
    TemplateCategory,
    TemplateStatus,
    WhatsAppTemplate,
)
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.core.interfaces.repositories.whatsapp_template_repository import (
    WhatsAppTemplateRepository,
)
from src.infrastructure.external_services.meta import TemplateClient


class CreateTemplateUseCase:
    """Use case for creating a WhatsApp template.

    Contract: POST /stores/{store_id}/channels/whatsapp/templates
    Body: { "name", "category", "language", "components": [...] }
    """

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
        whatsapp_template_repository: WhatsAppTemplateRepository,
    ):
        self.channel_connection_repository = channel_connection_repository
        self.whatsapp_template_repository = whatsapp_template_repository

    async def execute(
        self,
        connection_id: UUID,
        name: str,
        category: str,
        language: str,
        header: str | None = None,
        body: str | None = None,
        footer: str | None = None,
        buttons: list[dict] | None = None,
    ) -> WhatsAppTemplate:
        """Create a new WhatsApp template.

        Args:
            connection_id: Channel connection UUID
            name: Template name
            category: Template category (MARKETING, UTILITY, AUTHENTICATION)
            language: Template language code
            header: Optional header text
            body: Body text
            footer: Optional footer text
            buttons: Optional buttons

        Returns:
            Created template entity
        """
        connection = await self.channel_connection_repository.get_by_id(connection_id)
        if not connection:
            raise ValueError("Connection not found")

        from src.infrastructure.external_services.secrets.secrets_manager import (
            SecretsManager,
        )

        secrets = SecretsManager()
        if not connection.encrypted_credentials or not connection.credential_key_id:
            raise ValueError("Connection has no encrypted credentials")
        creds = await secrets.decrypt(
            connection.encrypted_credentials, connection.credential_key_id
        )
        access_token = creds.get("access_token", "")

        waba_id = (
            connection.external_phone_number_id or connection.external_account_id or ""
        )
        client = TemplateClient(waba_id=waba_id, access_token=access_token)

        try:
            components = client.build_text_template(header, body, footer, buttons)
            result = await client.create_template(
                name=name,
                category=category,
                language=language,
                components=components,
            )

            template = WhatsAppTemplate(
                tenant_id=connection.tenant_id,
                store_id=connection.store_id,
                channel_connection_id=connection_id,
                external_template_id=result.get("id"),
                name=name,
                category=TemplateCategory(category),
                language=language,
                status=TemplateStatus.PENDING,
                components={"components": components},
            )
            return await self.whatsapp_template_repository.create(template)
        finally:
            await client.close()


class ListTemplatesUseCase:
    """Use case for listing WhatsApp templates.

    Contract: GET /stores/{store_id}/channels/whatsapp/templates
    """

    def __init__(
        self,
        whatsapp_template_repository: WhatsAppTemplateRepository,
    ):
        self.whatsapp_template_repository = whatsapp_template_repository

    async def execute(
        self,
        channel_connection_id: UUID,
        status: str | None = None,
    ) -> list[WhatsAppTemplateDTO]:
        """List templates for a connection.

        Args:
            channel_connection_id: Channel connection UUID (from route path)
            status: Optional status filter

        Returns:
            List of template DTOs
        """
        template_status = TemplateStatus(status) if status else None
        templates = await self.whatsapp_template_repository.list_by_connection(
            channel_connection_id=channel_connection_id,
            status=template_status,
        )

        return [
            WhatsAppTemplateDTO(
                id=t.id,
                name=t.name,
                category=t.category.value,
                language=t.language,
                status=t.status.value,
                rejection_reason=t.rejection_reason,
            )
            for t in templates
        ]
