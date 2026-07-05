"""Pydantic v2 schemas for the WhatsApp connection / BYO surface.

Extends ``whatsapp.py`` with mode + BYO-credential schemas. Kept in a
separate file to avoid bloating the existing whatsapp.py module that the
merchant-hub UI already imports.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums.whatsapp import WhatsAppMode


class NotificationSettings(BaseModel):
    """Per-message-type WhatsApp notification toggles (FR-019a)."""

    model_config = ConfigDict(extra="allow")

    order_confirmation: bool = True
    payment_received: bool = True
    shipping_update: bool = True
    delivery_confirmation: bool = True
    abandoned_cart: bool = True
    marketing: bool = False
    # COD "tap to confirm" flow (backend-031, order_confirmation_request_v1).
    # Opt-in, so it defaults OFF. When ON, COD orders get the active confirm
    # request instead of the passive order_confirmation notice. Key matches
    # the merchant-hub contract.
    require_order_confirmation: bool = False


# Language the automated order-lifecycle notifications are sent in.
# "auto" follows the store's default_language; "ar"/"en" force every
# notification to that language regardless of the store default. Read +
# honored at send time in
# whatsapp_notification_handler._resolve_send_context. Persisted at
# store.settings.whatsapp.message_language.
MessageLanguage = Literal["auto", "ar", "en"]


class WhatsAppStatus(BaseModel):
    """Per-store WhatsApp connection status (GET /whatsapp/status)."""

    mode: WhatsAppMode
    connected: bool
    phone_display_name: str | None = None
    display_phone_number: str | None = None
    quality_rating: Literal["GREEN", "YELLOW", "RED", "UNKNOWN"] | None = None
    waba_id: str | None = Field(
        default=None,
        description="Exposed only for BYO mode; null for platform_managed.",
    )
    last_validated_at: datetime | None = None
    credential_error: str | None = Field(
        default=None,
        description="Set when last send failed with a credential-class Meta error; cleared on next successful validation.",
    )
    message_language: MessageLanguage = Field(
        default="auto",
        description=(
            "Language for automated order notifications. 'auto' follows the"
            " store default language; 'ar'/'en' force that language."
        ),
    )
    confirm_order_delay_minutes: int = Field(
        default=0,
        ge=0,
        description=(
            "Delay before the COD confirm-order request is sent. 0 = send"
            " immediately on order creation; >0 schedules it that many"
            " minutes later via the WhatsApp scheduled-send queue."
        ),
    )
    notifications: NotificationSettings


class WhatsAppSettingsUpdate(BaseModel):
    """Body for PATCH /whatsapp/settings — partial update of store-level
    WhatsApp preferences that are not per-message toggles."""

    message_language: MessageLanguage | None = None
    confirm_order_delay_minutes: int | None = Field(default=None, ge=0)


class BYOConnectRequest(BaseModel):
    """Body for POST /whatsapp/byo/connect."""

    access_token: str = Field(
        ...,
        min_length=1,
        description=(
            "Meta System User Access Token with whatsapp_business_management"
            " + whatsapp_business_messaging scopes."
        ),
    )
    phone_number_id: str = Field(..., min_length=1)
    waba_id: str = Field(..., min_length=1)
    app_secret: str = Field(..., min_length=1)


class BYOValidationFailure(BaseModel):
    """422 response for POST /whatsapp/byo/connect when Meta validation fails.

    Three steps in order: phone_metadata_read, waba_info_read, template_list_read.
    Whichever fails is identified here. Surfacing the Meta error is restricted
    to a whitelist of fields per TASK-SEC-009 (`code`, `error_subcode`, `message`,
    `type` only — `fbtrace_id` etc. dropped).
    """

    failed_step: Literal["phone_metadata_read", "waba_info_read", "template_list_read"]
    code: Literal[
        "phone_number_unreachable",
        "waba_mismatch",
        "insufficient_scope",
        "meta_api_unavailable",
        "unknown",
    ]
    message: str
    meta_error: dict | None = None


# ── WhatsApp access gate (platform entitlement) ──────────────────────────────
#
# Sits *above* the connection flow: a store must hold an APPROVED access row
# before it can connect a number, complete embedded signup, or enable order
# notifications. Surfaced to the merchant hub so it can render the
# request / pending / rejected / disabled states. ``none`` = no row yet.

WhatsAppAccessStatusLiteral = Literal[
    "none", "pending", "approved", "rejected", "disabled"
]


class WhatsAppAccessState(BaseModel):
    """Current WhatsApp access-gate state for a store (GET /whatsapp/access)."""

    status: WhatsAppAccessStatusLiteral = "none"
    note: str | None = None
    contact_phone: str | None = None
    expected_volume: str | None = None
    requested_at: datetime | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = Field(
        default=None,
        description="Admin's reason on reject/disable; safe to show the merchant.",
    )
    can_request: bool = Field(
        default=True,
        description=(
            "True when the merchant may (re)submit a request — i.e. there is no"
            " row yet, or the previous request was rejected."
        ),
    )


class WhatsAppAccessRequestBody(BaseModel):
    """Body for POST /whatsapp/access/request (merchant submits the form)."""

    note: str | None = Field(
        default=None,
        max_length=2000,
        description="Merchant's use-case / why they want WhatsApp.",
    )
    contact_phone: str | None = Field(default=None, max_length=32)
    expected_volume: str | None = Field(
        default=None,
        max_length=64,
        description="Free-text expected monthly message volume (e.g. '1-500').",
    )


class CheckoutSessionIssueRequest(BaseModel):
    """Body for POST /storefront/{store_slug}/checkout-session (FR-007b)."""

    phone: str = Field(
        ...,
        description="Will be canonicalized to E.164 and stored on the session.",
    )
    locale: str = "ar"


class CheckoutSessionIssueResponse(BaseModel):
    """Response from POST /storefront/{store_slug}/checkout-session."""

    token: str = Field(
        ..., description="Opaque UUID; pass back to phone-bound endpoints."
    )
    expires_at: datetime
