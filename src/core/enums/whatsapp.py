"""WhatsApp domain enums — used by the pure-logic guard + detector and the
infrastructure layer messaging service.
"""

from enum import StrEnum


class TemplateCategory(StrEnum):
    """Meta WhatsApp template category. Drives the two-tier opt-in policy
    (FR-011): utility/auth respect opt-out but bypass active opt-in
    requirement; marketing requires both.
    """

    UTILITY = "UTILITY"
    MARKETING = "MARKETING"
    AUTHENTICATION = "AUTHENTICATION"


class SendSkipReason(StrEnum):
    """Structured reason codes returned by ``WhatsAppSendGuard`` (FR-038).

    Logged on every guard-rejected send so SC-003 / SC-012 can be measured.
    """

    NO_PHONE = "no_phone"
    INVALID_PHONE = "invalid_phone"
    NO_CREDENTIALS = "no_credentials"
    CREDENTIALS_INVALID = "credentials_invalid"
    MERCHANT_SETTING_OFF = "merchant_setting_off"
    OPT_OUT = "opt_out"
    NO_OPT_IN = "no_opt_in"
    WINDOW_CLOSED = "window_closed"
    TEMPLATE_NOT_APPROVED = "template_not_approved"
    ALREADY_SENT = "already_sent"


class OptInSource(StrEnum):
    """How a ``whatsapp_opt_ins`` row entered the system."""

    CHECKOUT = "checkout"
    SIGNUP = "signup"
    IMPORT = "import"
    API = "api"
    INBOUND_REPLY = "inbound_reply"


class OptOutReason(StrEnum):
    """Why an opt-in row was revoked."""

    INBOUND_STOP_KEYWORD = "inbound_stop_keyword"
    MERCHANT_REVOKE = "merchant_revoke"
    CUSTOMER_REQUEST_VIA_SUPPORT = "customer_request_via_support"
    API_REVOKE = "api_revoke"


class WhatsAppMode(StrEnum):
    """Per-store WhatsApp operating mode (FR-019)."""

    PLATFORM_MANAGED = "platform_managed"
    BYO = "byo"
