"""Omnichannel DTOs."""

from uuid import UUID

from pydantic import BaseModel


class ConnectMetaDTO(BaseModel):
    """Request DTO for starting Meta OAuth."""

    store_id: UUID
    redirect_uri: str


class ConnectMetaCallbackDTO(BaseModel):
    """Request DTO for OAuth callback."""

    code: str
    state: str
    redirect_uri: str


class DisconnectChannelDTO(BaseModel):
    """Request DTO for disconnecting a channel."""

    store_id: UUID
    connection_id: UUID


class SendMessageDTO(BaseModel):
    """Request DTO for sending a message."""

    thread_id: UUID
    message: str
    attachment_type: str | None = None
    attachment_url: str | None = None
    template_name: str | None = None
    template_params: dict | None = None


class IngestMessageDTO(BaseModel):
    """Request DTO for ingesting inbound message."""

    connection_id: UUID
    external_message_id: str
    sender_id: str
    sender_name: str | None = None
    message_type: str
    body: str | None = None
    attachment_url: str | None = None
    timestamp: int


class ListThreadsDTO(BaseModel):
    """Request DTO for listing threads."""

    store_id: UUID
    channel: str | None = None
    status: str | None = None
    skip: int = 0
    limit: int = 50


class GetThreadDTO(BaseModel):
    """Request DTO for getting a thread."""

    thread_id: UUID


class ListMessagesDTO(BaseModel):
    """Request DTO for listing messages."""

    thread_id: UUID
    skip: int = 0
    limit: int = 100


class MarkReadDTO(BaseModel):
    """Request DTO for marking thread as read."""

    thread_id: UUID


class ResolveThreadDTO(BaseModel):
    """Request DTO for resolving a thread."""

    thread_id: UUID


class CreateTemplateDTO(BaseModel):
    """Request DTO for creating WhatsApp template."""

    connection_id: UUID
    name: str
    category: str
    language: str
    header: str | None = None
    body: str | None = None
    footer: str | None = None
    buttons: list[dict] | None = None


class ListTemplatesDTO(BaseModel):
    """Request DTO for listing templates."""

    connection_id: UUID
    status: str | None = None


class SyncCatalogDTO(BaseModel):
    """Request DTO for triggering catalog sync."""

    connection_id: UUID
    full_sync: bool = False


class SendCapiEventDTO(BaseModel):
    """Request DTO for sending CAPI event."""

    store_id: UUID
    event_name: str
    event_id: UUID
    event_time: int
    user_data: dict
    custom_data: dict | None = None
    event_source_url: str | None = None


class ChannelConnectionDTO(BaseModel):
    """Response DTO for channel connection."""

    id: UUID
    channel: str
    status: str
    external_account_id: str | None
    external_account_name: str | None
    external_phone_number_id: str | None
    is_active: bool
    token_expires_at: str | None


class MessageThreadDTO(BaseModel):
    """Response DTO for message thread."""

    id: UUID
    channel: str
    participant_name: str | None
    participant_avatar_url: str | None
    participant_phone: str | None
    status: str
    last_message_preview: str | None
    last_message_at: str | None
    unread_count: int


class ChannelMessageDTO(BaseModel):
    """Response DTO for channel message."""

    id: UUID
    direction: str
    type: str
    body: str | None
    attachment_url: str | None
    status: str
    created_at: str
    sender_name: str | None


class WhatsAppTemplateDTO(BaseModel):
    """Response DTO for WhatsApp template."""

    id: UUID
    name: str
    category: str
    language: str
    status: str
    rejection_reason: str | None
