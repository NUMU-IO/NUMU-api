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
    notifications: NotificationSettings


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
