"""WhatsApp Business API schemas for merchant dashboard."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ── Enums ──


class ConnectionType(StrEnum):
    """How the store is connected to WhatsApp."""

    SHARED = "shared"  # Using NUMU's shared number
    OWN = "own"  # Merchant's own WABA via embedded signup


class TemplateCategory(StrEnum):
    UTILITY = "UTILITY"
    MARKETING = "MARKETING"
    AUTHENTICATION = "AUTHENTICATION"


class TemplateStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAUSED = "PAUSED"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    SPAM = "spam"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Embedded Signup ──


class EmbeddedSignupConfig(BaseModel):
    """Config returned to frontend for Meta Embedded Signup JS SDK."""

    app_id: str
    config_id: str
    enabled: bool = True


class EmbeddedSignupRequest(BaseModel):
    """Code received from Meta's embedded signup callback."""

    code: str = Field(..., min_length=1)


class EmbeddedSignupResponse(BaseModel):
    """Result of completing embedded signup."""

    connected: bool
    phone_number: str | None = None
    display_name: str | None = None
    waba_id: str | None = None


# ── Connection Status ──


class WhatsAppConnectionStatus(BaseModel):
    """WhatsApp connection status for the store."""

    connected: bool = False
    connection_type: ConnectionType = ConnectionType.SHARED
    phone_number: str | None = None
    phone_display_name: str | None = None
    waba_id: str | None = None
    quality_rating: str | None = None  # GREEN / YELLOW / RED
    messaging_limit: str | None = None  # e.g., "1K", "10K", "100K"
    connected_at: str | None = None


# ── Notification Settings ──


class NotificationToggle(BaseModel):
    """Individual notification type toggle."""

    enabled: bool = False
    template_name: str | None = None
    last_sent_at: str | None = None
    sent_count_30d: int = 0


class NotificationSettings(BaseModel):
    """All notification toggles."""

    order_confirmation: NotificationToggle = Field(
        default_factory=lambda: NotificationToggle(enabled=True)
    )
    order_shipped: NotificationToggle = Field(
        default_factory=lambda: NotificationToggle(enabled=True)
    )
    out_for_delivery: NotificationToggle = Field(default_factory=NotificationToggle)
    order_delivered: NotificationToggle = Field(
        default_factory=lambda: NotificationToggle(enabled=True)
    )
    payment_received: NotificationToggle = Field(default_factory=NotificationToggle)
    abandoned_cart: NotificationToggle = Field(default_factory=NotificationToggle)


class UpdateNotificationSettingsRequest(BaseModel):
    """Update notification toggles (partial update)."""

    order_confirmation: bool | None = None
    order_shipped: bool | None = None
    out_for_delivery: bool | None = None
    order_delivered: bool | None = None
    payment_received: bool | None = None
    abandoned_cart: bool | None = None


# ── Analytics ──


class WhatsAppDayStat(BaseModel):
    """Stats for a single day."""

    date: str
    sent: int = 0
    delivered: int = 0
    read: int = 0
    failed: int = 0


class WhatsAppAnalytics(BaseModel):
    """Aggregated WhatsApp analytics."""

    period: str  # "7d", "30d", "90d"
    total_sent: int = 0
    total_delivered: int = 0
    total_read: int = 0
    total_failed: int = 0
    delivery_rate: float = 0.0
    read_rate: float = 0.0
    active_conversations: int = 0
    avg_response_time_minutes: float | None = None
    daily_stats: list[WhatsAppDayStat] = []
    by_template: dict[str, dict[str, int]] = {}


# ── Templates ──


class TemplateButton(BaseModel):
    """Template button definition."""

    type: str  # "URL" | "QUICK_REPLY" | "PHONE_NUMBER"
    text: str
    url: str | None = None
    phone_number: str | None = None


class TemplateCreate(BaseModel):
    """Create a new WhatsApp message template."""

    name: str = Field(..., min_length=1, max_length=512, pattern=r"^[a-z0-9_]+$")
    language: str = Field(default="ar", max_length=10)
    category: TemplateCategory = TemplateCategory.UTILITY
    header_type: str | None = Field(None, max_length=20)  # TEXT, IMAGE, VIDEO, DOCUMENT
    header_content: str | None = Field(None, max_length=500)
    body_text: str = Field(..., min_length=1, max_length=1024)
    footer_text: str | None = Field(None, max_length=60)
    buttons: list[TemplateButton] = []


class TemplateResponse(BaseModel):
    """WhatsApp template details."""

    id: UUID
    store_id: UUID
    meta_template_id: str | None = None
    name: str
    language: str
    category: TemplateCategory
    status: TemplateStatus
    header_type: str | None = None
    header_content: str | None = None
    body_text: str
    footer_text: str | None = None
    buttons: list[TemplateButton] = []
    is_system: bool = False  # True for NUMU default templates
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class TemplateListResponse(BaseModel):
    """List of templates."""

    templates: list[TemplateResponse]
    total: int


# ── Conversations ──


class ConversationSummary(BaseModel):
    """Conversation list item for inbox view."""

    id: UUID
    customer_phone: str
    customer_name: str | None = None
    customer_id: UUID | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    last_message_direction: str | None = None  # "inbound" | "outbound"
    unread_count: int = 0
    status: ConversationStatus = ConversationStatus.ACTIVE
    assigned_to: UUID | None = None
    window_open: bool = False  # True if 24h window is still open
    window_expires_at: datetime | None = None


class ConversationListResponse(BaseModel):
    """Paginated conversation list."""

    conversations: list[ConversationSummary]
    total: int
    unread_total: int = 0


class MessageBubble(BaseModel):
    """Single message in a conversation thread."""

    id: UUID
    message_id: str
    direction: str  # "inbound" | "outbound"
    content: str | None = None
    template_name: str | None = None
    status: str  # queued, sent, delivered, read, failed
    created_at: datetime
    metadata: dict[str, Any] | None = None


class MessageListResponse(BaseModel):
    """Paginated message thread."""

    messages: list[MessageBubble]
    total: int
    window_open: bool = False
    window_expires_at: datetime | None = None


class SendMessageRequest(BaseModel):
    """Send a message in a conversation."""

    text: str | None = Field(None, max_length=4096)
    template_id: UUID | None = None
    template_params: dict[str, str] | None = None
    media_url: str | None = None
    media_caption: str | None = None


class ConversationUpdateRequest(BaseModel):
    """Update conversation (mark read, archive, assign)."""

    status: ConversationStatus | None = None
    assigned_to: UUID | None = None
    mark_read: bool | None = None


class UnreadCountResponse(BaseModel):
    """Unread conversation count for sidebar badge."""

    unread_count: int = 0


# ── Campaigns ──


class AudienceFilter(BaseModel):
    """Audience filter for campaigns."""

    type: str = "all"  # all, has_phone, recent_buyers, inactive, custom
    ordered_within_days: int | None = None
    inactive_days: int | None = None
    min_total_spent: int | None = None  # in cents
    governorate: str | None = None
    tags: list[str] | None = None
    opted_in_marketing: bool = True


class AudienceEstimate(BaseModel):
    """Audience size estimate before sending."""

    estimated_count: int
    sample_recipients: list[dict[str, str]] = []  # [{name, phone}]


class CampaignCreate(BaseModel):
    """Create a new campaign."""

    name: str = Field(..., min_length=1, max_length=255)
    template_id: UUID
    audience_filter: AudienceFilter = Field(default_factory=AudienceFilter)
    template_params: dict[str, str] | None = None


class CampaignUpdate(BaseModel):
    """Update a draft campaign."""

    name: str | None = Field(None, min_length=1, max_length=255)
    template_id: UUID | None = None
    audience_filter: AudienceFilter | None = None
    template_params: dict[str, str] | None = None


class CampaignScheduleRequest(BaseModel):
    """Schedule a campaign for later."""

    scheduled_at: datetime


class CampaignResponse(BaseModel):
    """Campaign details."""

    id: UUID
    store_id: UUID
    name: str
    template_id: UUID
    template_name: str | None = None
    audience_filter: AudienceFilter
    status: CampaignStatus
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_recipients: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    read_count: int = 0
    failed_count: int = 0
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class CampaignListResponse(BaseModel):
    """List of campaigns."""

    campaigns: list[CampaignResponse]
    total: int


class CampaignRecipientResponse(BaseModel):
    """Per-recipient delivery status."""

    customer_id: UUID | None = None
    phone: str
    customer_name: str | None = None
    status: str  # pending, sent, delivered, read, failed
    message_id: str | None = None
    sent_at: datetime | None = None


class CampaignRecipientsListResponse(BaseModel):
    """Paginated campaign recipients."""

    recipients: list[CampaignRecipientResponse]
    total: int
