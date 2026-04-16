"""Send message use case."""

from datetime import UTC
from uuid import UUID

from src.core.entities.channel_connection import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
)
from src.core.entities.channel_message import (
    ChannelMessage,
    MessageDirection,
    MessageStatus,
    MessageType,
)
from src.core.entities.message_thread import MessageThread
from src.core.exceptions import BusinessRuleViolationError, ValidationError
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.core.interfaces.repositories.channel_message_repository import (
    ChannelMessageRepository,
)
from src.core.interfaces.repositories.message_thread_repository import (
    MessageThreadRepository,
)
from src.infrastructure.external_services.meta import (
    InstagramClient,
    MessengerClient,
    WhatsAppClient,
)


class SendMessageUseCase:
    """Use case for sending outbound messages."""

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
        message_thread_repository: MessageThreadRepository,
        channel_message_repository: ChannelMessageRepository,
    ):
        self.channel_connection_repository = channel_connection_repository
        self.message_thread_repository = message_thread_repository
        self.channel_message_repository = channel_message_repository

    async def execute(
        self,
        thread_id: UUID,
        message: str,
        attachment_type: str | None = None,
        attachment_url: str | None = None,
        template_name: str | None = None,
        template_params: dict | None = None,
    ) -> ChannelMessage:
        """Send a message to a thread.

        Args:
            thread_id: The thread UUID
            message: Message text
            attachment_type: Type of attachment (image, video, document)
            attachment_url: URL of attachment
            template_name: WhatsApp template name
            template_params: Template parameters

        Returns:
            Created message entity
        """
        thread = await self.message_thread_repository.get_by_id(thread_id)
        if not thread:
            raise ValidationError("Thread not found")

        connection = await self.channel_connection_repository.get_by_id(
            thread.channel_connection_id
        )
        if not connection or connection.status != ConnectionStatus.ACTIVE:
            raise BusinessRuleViolationError("Channel connection not active")

        if connection.channel == ChannelType.WHATSAPP:
            await self._check_24_hour_window(thread, connection)
            await self._send_whatsapp(
                connection,
                thread,
                message,
                attachment_type,
                attachment_url,
                template_name,
                template_params,
            )
        elif connection.channel == ChannelType.FACEBOOK:
            await self._send_messenger(
                connection, thread, message, attachment_type, attachment_url
            )
        elif connection.channel == ChannelType.INSTAGRAM:
            await self._send_instagram(
                connection, thread, message, attachment_type, attachment_url
            )
        else:
            raise ValidationError(f"Unknown channel: {connection.channel}")

        msg_type = MessageType.TEXT
        if attachment_type:
            msg_type = (
                MessageType(attachment_type)
                if attachment_type in [t.value for t in MessageType]
                else MessageType.IMAGE
            )
        elif template_name:
            msg_type = MessageType.TEMPLATE

        outbound_msg = ChannelMessage(
            tenant_id=thread.tenant_id,
            thread_id=thread_id,
            direction=MessageDirection.OUTBOUND,
            channel=connection.channel,
            sender_external_id=connection.external_account_id,
            type=msg_type,
            body=message,
            attachment_url=attachment_url,
            template_name=template_name,
            template_payload=template_params,
            status=MessageStatus.SENT,
        )
        await self.channel_message_repository.create(outbound_msg)

        thread.last_message_at = outbound_msg.created_at
        thread.last_message_preview = message[:100] if message else "Sent"
        await self.message_thread_repository.update(thread)

        return outbound_msg

    async def _check_24_hour_window(
        self, thread: MessageThread, connection: ChannelConnection
    ) -> None:
        """Check WhatsApp 24-hour window."""
        if thread.last_message_at:
            from datetime import datetime

            last_msg_time = thread.last_message_at
            if isinstance(last_msg_time, str):
                last_msg_time = datetime.fromisoformat(
                    last_msg_time.replace("Z", "+00:00")
                )
            hours_since = (datetime.now(UTC) - last_msg_time).total_seconds() / 3600
            if hours_since > 24 and not connection.status:
                raise BusinessRuleViolationError(
                    "Outside 24-hour window. Use a template message."
                )

    async def _send_whatsapp(
        self,
        connection: ChannelConnection,
        thread: MessageThread,
        message: str,
        attachment_type: str | None,
        attachment_url: str | None,
        template_name: str | None,
        template_params: dict | None,
    ) -> None:
        from src.infrastructure.external_services.secrets import SecretsManager

        secrets = SecretsManager()
        access_token = ""
        if connection.encrypted_credentials and connection.credential_key_id:
            decrypted = await secrets.decrypt(
                connection.encrypted_credentials, connection.credential_key_id
            )
            access_token = decrypted.get("access_token", "")

        client = WhatsAppClient(
            phone_number_id=connection.external_phone_number_id or "",
            access_token=access_token,
        )

        try:
            if template_name:
                await client.send_template(
                    recipient_phone=thread.participant_phone_e164 or "",
                    template_name=template_name,
                    language="ar_AR",
                    components=template_params.get("components")
                    if template_params
                    else None,
                )
            elif attachment_type == "image":
                await client.send_image(
                    recipient_phone=thread.participant_phone_e164 or "",
                    image_url=attachment_url,
                )
            else:
                await client.send_text(
                    recipient_phone=thread.participant_phone_e164 or "",
                    text=message,
                )
        finally:
            await client.close()

    async def _send_messenger(
        self,
        connection: ChannelConnection,
        thread: MessageThread,
        message: str,
        attachment_type: str | None,
        attachment_url: str | None,
    ) -> None:
        from src.infrastructure.external_services.secrets import SecretsManager

        secrets = SecretsManager()
        access_token = ""
        if connection.encrypted_credentials and connection.credential_key_id:
            decrypted = await secrets.decrypt(
                connection.encrypted_credentials, connection.credential_key_id
            )
            access_token = decrypted.get("access_token", "")

        client = MessengerClient(
            page_id=connection.external_account_id or "",
            page_access_token=access_token,
        )

        try:
            if attachment_url:
                await client.send_attachment(
                    recipient_id=thread.external_participant_id,
                    attachment_type=attachment_type or "image",
                    attachment_url=attachment_url,
                )
            else:
                await client.send_text(
                    recipient_id=thread.external_participant_id,
                    text=message,
                )
        finally:
            await client.close()

    async def _send_instagram(
        self,
        connection: ChannelConnection,
        thread: MessageThread,
        message: str,
        attachment_type: str | None,
        attachment_url: str | None,
    ) -> None:
        from src.infrastructure.external_services.secrets import SecretsManager

        secrets = SecretsManager()
        access_token = ""
        if connection.encrypted_credentials and connection.credential_key_id:
            decrypted = await secrets.decrypt(
                connection.encrypted_credentials, connection.credential_key_id
            )
            access_token = decrypted.get("access_token", "")

        client = InstagramClient(
            ig_user_id=connection.external_account_id or "",
            access_token=access_token,
        )

        try:
            if attachment_url:
                att_type = "image" if attachment_type in ("image", "video") else "image"
                await client.send_attachment(
                    recipient_igid=thread.external_participant_id,
                    attachment_type=att_type,
                    attachment_url=attachment_url,
                )
            else:
                await client.send_text(
                    recipient_igid=thread.external_participant_id,
                    text=message,
                )
        finally:
            await client.close()
