"""Pydantic models for Meta webhook payloads."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MetaWebhookEntryType(StrEnum):
    MESSAGING = "messaging"
    CHANGES = "changes"


class MetaMessageType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "audio"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"
    STORY = "story"
    SYSTEM = "system"


class MetaMessagingType(StrEnum):
    RESPONSE = "response"
    UPDATE = "update"
    MESSAGE_TAG = "message_tag"


class MetaWebhookEntry(BaseModel):
    """Meta webhook entry."""

    id: str
    time: int
    messaging: list[dict[str, Any]] | None = None
    changes: list[dict[str, Any]] | None = None


class MetaWebhookPayload(BaseModel):
    """Root Meta webhook payload."""

    object: str = "page"
    entry: list[MetaWebhookEntry] = Field(default_factory=list)


class MetaMessageSender(BaseModel):
    """Message sender (user who sent)."""

    id: str


class MetaMessageRecipient(BaseModel):
    """Message recipient (the Page)."""

    id: str


class MetaMessage(BaseModel):
    """Meta message object."""

    mid: str = Field(alias="mid")
    message_id: str | None = Field(default=None, alias="mid")
    text: str | None = None
    attachments: list[dict[str, Any]] | None = None
    quick_reply: dict[str, Any] | None = None
    refer: dict[str, Any] | None = None
    is_echo: bool | None = None
    app_id: str | None = None
    metadata: str | None = None

    class Config:
        populate_by_name = True


class MetaMessagingItem(BaseModel):
    """Messaging event item."""

    sender: MetaMessageSender
    recipient: MetaMessageRecipient
    timestamp: int
    message: MetaMessage | None = None
    postback: dict[str, Any] | None = None
    optin: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    read: dict[str, Any] | None = None


class WhatsAppMessageType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"
    INTERACTIVE = "interactive"
    CONTEXT = "context"
    BUTTONS = "button"


class WhatsAppIdentity(BaseModel):
    """WhatsApp identity."""

    number: str


class WhatsAppFrom(BaseModel):
    """WhatsApp from (sender)."""

    phone: str


class WhatsAppContext(BaseModel):
    """WhatsApp context (reply to)."""

    from_field: WhatsAppFrom | None = Field(default=None, validation_alias="from")
    message_id: str | None = Field(default=None, validation_alias="id")


class WhatsAppText(BaseModel):
    """WhatsApp text message."""

    body: str


class WhatsAppImage(BaseModel):
    """WhatsApp image."""

    id: str | None = None
    link: str | None = None
    mime_type: str | None = None
    sha256: str | None = None


class WhatsAppVideo(BaseModel):
    """WhatsApp video."""

    id: str | None = None
    link: str | None = None
    mime_type: str | None = None


class WhatsAppDocument(BaseModel):
    """WhatsApp document."""

    id: str | None = None
    link: str | None = None
    filename: str | None = None
    mime_type: str | None = None


class WhatsAppAudio(BaseModel):
    """WhatsApp audio."""

    id: str | None = None
    link: str | None = None
    mime_type: str | None = None


class WhatsAppInteractive(BaseModel):
    """WhatsApp interactive message."""

    type: str
    list_reply: dict[str, Any] | None = None
    button_reply: dict[str, Any] | None = None


class WhatsAppMessage(BaseModel):
    """WhatsApp message."""

    from_field: str = Field(validation_alias="from")
    id: str
    timestamp: str
    type: WhatsAppMessageType
    text: WhatsAppText | None = Field(default=None, validation_alias="text")
    image: WhatsAppImage | None = Field(default=None, validation_alias="image")
    video: WhatsAppVideo | None = Field(default=None, validation_alias="video")
    audio: WhatsAppAudio | None = Field(default=None, validation_alias="audio")
    document: WhatsAppDocument | None = Field(default=None, validation_alias="document")
    sticker: dict[str, Any] | None = None
    interactive: WhatsAppInteractive | None = Field(
        default=None, validation_alias="interactive"
    )
    context: WhatsAppContext | None = Field(default=None, validation_alias="context")
    button: dict[str, Any] | None = None
    system: dict[str, Any] | None = None

    class Config:
        populate_by_name = True


class WhatsAppMetadata(BaseModel):
    """WhatsApp message metadata."""

    display_phone_number: str
    phone_number_id: str


class WhatsAppEntry(BaseModel):
    """WhatsApp webhook entry."""

    id: str
    changes: list[dict[str, Any]] = Field(default_factory=list)


class WhatsAppWebhookPayload(BaseModel):
    """Root WhatsApp webhook payload."""

    object: str = "whatsapp_business_account"
    entry: list[WhatsAppEntry] = Field(default_factory=list)


class WhatsAppStatus(BaseModel):
    """WhatsApp status update (delivery, read)."""

    id: str
    status: str
    timestamp: str
    recipient_id: str


class WhatsAppStatusPayload(BaseModel):
    """WhatsApp status webhook payload."""

    object: str = "whatsapp_business_account"
    entry: list[WhatsAppEntry] = Field(default_factory=list)


class MetaDeliveryStatus(StrEnum):
    SENT = "sent"
    DELIVERED = "read"
    READ = "read"
    FAILED = "failed"


class MetaMessageStatus(BaseModel):
    """Meta message status (delivery/read)."""

    mid: str
    sender: str
    recipient: str
    status: str
    timestamp: int


class MetaPageStory(BaseModel):
    """Facebook Page story."""

    link: str | None = None


class MetaMessageRequest(BaseModel):
    """Request model for sending a message."""

    recipient_id: str
    message: str
    attachment_type: str | None = None
    attachment_url: str | None = None
    template_name: str | None = None
    template_params: dict[str, Any] | None = None


class MetaConversationRequest(BaseModel):
    """Request model for starting a conversation."""

    channel: str
    page_id: str | None = None
    phone_number_id: str | None = None
    recipient: str
    message: str | None = None
    template_name: str | None = None
    template_language: str = "ar_AR"
    template_components: list[dict[str, Any]] | None = None
