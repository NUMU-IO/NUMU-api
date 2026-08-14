"""Messaging service interface for customer notifications."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MessageChannel(StrEnum):
    """Message delivery channels."""

    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class MessageType(StrEnum):
    """Predefined message types."""

    ORDER_CONFIRMATION = "order_confirmation"
    # Active "tap to confirm" request for COD orders (distinct from the
    # passive ORDER_CONFIRMATION notice). Carries a quick-reply button whose
    # payload encodes the order so the inbound webhook can confirm it.
    ORDER_CONFIRMATION_REQUEST = "order_confirmation_request"
    ORDER_SHIPPED = "order_shipped"
    OUT_FOR_DELIVERY = "out_for_delivery"
    ORDER_DELIVERED = "order_delivered"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_FAILED = "payment_failed"
    DELIVERY_FAILED = "delivery_failed"
    ORDER_CANCELLED = "order_cancelled"
    REFUND_PROCESSED = "refund_processed"
    WELCOME = "welcome"
    PASSWORD_RESET = "password_reset"
    CUSTOM = "custom"
    ABANDONED_CART = "abandoned_cart"
    # COD-to-prepaid recovery offer (the "recover" cod_trust flow).
    COD_RECOVERY_OFFER = "cod_recovery_offer"
    # COD Autopilot (004-cod-autopilot): daily ship digest to the MERCHANT.
    SHIP_DIGEST = "ship_digest"
    # COD Autopilot: post-shipped delivery check to the CUSTOMER.
    DELIVERY_CHECK = "delivery_check"
    # Phone-first checkout identity: the verification code itself.
    # Customer-initiated (they clicked "send code"), AUTHENTICATION-tier —
    # delivered over GOWA as locally-rendered plain text; the Meta path
    # needs an approved AUTH template (special OTP component) and is not
    # wired yet — see application/services/checkout_identity.otp_available.
    OTP_VERIFICATION = "otp_verification"


class MessageStatus(StrEnum):
    """Message delivery status."""

    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


@dataclass
class MessageTemplate:
    """Message template definition."""

    type: MessageType
    name: str  # Template name in WhatsApp Business
    language: str = "en"
    components: list[dict] = field(default_factory=list)


@dataclass
class MessageRecipient:
    """Message recipient information."""

    phone: str  # Phone number with country code (e.g., +201234567890)
    name: str | None = None
    email: str | None = None
    language: str = "en"  # Preferred language


@dataclass
class MessageContent:
    """Message content to send."""

    type: MessageType
    recipient: MessageRecipient
    template_params: dict[str, Any] = field(default_factory=dict)
    channel: MessageChannel = MessageChannel.WHATSAPP


@dataclass
class MessageResult:
    """Result of sending a message."""

    success: bool
    message_id: str | None = None
    channel: MessageChannel = MessageChannel.WHATSAPP
    status: MessageStatus = MessageStatus.PENDING
    error_message: str | None = None
    error_code: str | None = None


# Predefined templates for the Egyptian market — the canonical mapping
# between NUMU's MessageType enum and the templates submitted to Meta on
# the platform-managed WABA. Each entry must match Meta's submitted body
# (placeholder count + order) and CTA structure exactly; otherwise the
# /messages POST returns (#132012) Parameter format does not match.
#
# Naming convention: templates submitted with URL CTA buttons live under
# a ``_v2`` suffix because Meta locks deleted names for 30 days after
# deletion (the dashboard says "Try again in less than 1 minute" but the
# actual cooldown is much longer). The five button-less templates
# (payment_received, order_delivered, optout_confirmation/en) were never
# deleted so they keep their original names.
#
# Param positions are 1-indexed in Meta's template body. The list order
# below determines the {{1}}/{{2}}/{{3}}... ordering — DO NOT reorder
# without also reordering the Meta-side template.
#
# Body parameters are passed as positional values via ``template_params``.
# URL button parameters (when ``url_button`` is populated) are sourced
# from the same dict by the named key listed under ``url_button["params"]``.
EGYPTIAN_TEMPLATES = {
    MessageType.ORDER_CONFIRMATION: {
        "en": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION,
            # Meta submission language was en_US per Meta's locale list.
            name="order_confirmation_v3",
            language="en_US",
            components=[
                # Body (rich): greeting + bold "Order summary" header + emoji
                # detail lines. {{1}} name, {{2}} store, {{3}} order number,
                # {{4}} total, {{5}} payment label.
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "store_name",
                        "order_number",
                        "total",
                        "payment_label",
                    ],
                },
                # URL button: Track order → https://numueg.app/o/{{1}}
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["order_id"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION,
            name="order_confirmation_v3",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "store_name",
                        "order_number",
                        "total",
                        "payment_label",
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["order_id"],
                },
            ],
        ),
    },
    MessageType.ORDER_CONFIRMATION_REQUEST: {
        "en": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION_REQUEST,
            # Meta submission language was en_US per Meta's locale list.
            name="order_confirmation_request_v2",
            language="en_US",
            components=[
                # Body (rich): greeting + bold "Order details" header + emoji
                # detail lines. {{1}} name, {{2}} store, {{3}} order number,
                # {{4}} total, {{5}} payment label, {{6}} item count,
                # {{7}} delivery address.
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "store_name",
                        "order_number",
                        "total",
                        "payment_label",
                        "item_count",
                        "address",
                    ],
                },
                # Three quick-reply buttons. Each param is a payload (not
                # display text) echoed back to us in the inbound webhook; the
                # ``<action>:<subdomain>/<order_id>`` prefix tells the webhook
                # which action the customer tapped (confirm/postpone/cancel).
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": ["confirm_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "1",
                    "parameters": ["postpone_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "2",
                    "parameters": ["cancel_payload"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION_REQUEST,
            name="order_confirmation_request_v2",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "store_name",
                        "order_number",
                        "total",
                        "payment_label",
                        "item_count",
                        "address",
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": ["confirm_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "1",
                    "parameters": ["postpone_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "2",
                    "parameters": ["cancel_payload"],
                },
            ],
        ),
    },
    MessageType.ORDER_SHIPPED: {
        "en": MessageTemplate(
            type=MessageType.ORDER_SHIPPED,
            name="order_shipped_v3",
            language="en",
            components=[
                # Body (rich): {{1}} name, {{2}} order number, {{3}} carrier,
                # {{4}} tracking number.
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "carrier",
                        "tracking_number",
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["order_id"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_SHIPPED,
            name="order_shipped_v3",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "carrier",
                        "tracking_number",
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["order_id"],
                },
            ],
        ),
    },
    MessageType.OUT_FOR_DELIVERY: {
        # No Meta template submitted yet — sends to this type will fail at
        # the API layer with a template-not-found error until one is
        # submitted. Kept here so MessageType.OUT_FOR_DELIVERY remains a
        # valid lookup key for code that already references it.
        "en": MessageTemplate(
            type=MessageType.OUT_FOR_DELIVERY,
            name="out_for_delivery_en",
            language="en",
            components=[
                {"type": "body", "parameters": ["customer_name", "order_number"]},
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.OUT_FOR_DELIVERY,
            name="out_for_delivery_ar",
            language="ar",
            components=[
                {"type": "body", "parameters": ["customer_name", "order_number"]},
            ],
        ),
    },
    MessageType.ORDER_DELIVERED: {
        "en": MessageTemplate(
            type=MessageType.ORDER_DELIVERED,
            name="order_delivered_v2",
            language="en",
            components=[
                # Body (rich): {{1}} name, {{2}} order number, {{3}} store.
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "store_name"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_DELIVERED,
            name="order_delivered_v2",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "store_name"],
                },
            ],
        ),
    },
    MessageType.PAYMENT_RECEIVED: {
        "en": MessageTemplate(
            type=MessageType.PAYMENT_RECEIVED,
            name="payment_received_v2",
            language="en",
            components=[
                # Body (rich): {{1}} name, {{2}} order number, {{3}} amount.
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "amount"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.PAYMENT_RECEIVED,
            name="payment_received_v2",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "amount"],
                },
            ],
        ),
    },
    MessageType.ABANDONED_CART: {
        "en": MessageTemplate(
            type=MessageType.ABANDONED_CART,
            name="abandoned_cart_v3",
            language="en",
            components=[
                # Body: Hi {{1}}, you left items in your cart at {{2}}.
                # Don't miss out — they may sell out soon!
                {
                    "type": "body",
                    "parameters": ["customer_name", "store_name"],
                },
                # URL button: Complete purchase → https://numueg.app/cart/{{1}}
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["cart_token"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ABANDONED_CART,
            name="abandoned_cart_v3",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "store_name"],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["cart_token"],
                },
            ],
        ),
    },
    MessageType.COD_RECOVERY_OFFER: {
        "en": MessageTemplate(
            type=MessageType.COD_RECOVERY_OFFER,
            name="cod_recovery_offer_v1",
            language="en",
            components=[
                # Body: {{1}} name, {{2}} order number, {{3}} store, {{4}} total,
                # {{5}} promo. URL button suffix: pay_payload ("<sub>/<order_id>").
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "store_name",
                        "total",
                        "promo",
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["pay_payload"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.COD_RECOVERY_OFFER,
            name="cod_recovery_offer_v1",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "store_name",
                        "total",
                        "promo",
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": ["pay_payload"],
                },
            ],
        ),
    },
    # ─── COD Autopilot (004-cod-autopilot) ────────────────────────────
    MessageType.SHIP_DIGEST: {
        "en": MessageTemplate(
            type=MessageType.SHIP_DIGEST,
            name="cod_ship_digest_v1",
            language="en_US",
            components=[
                # Body: {{1}} store name, {{2}} order count, {{3}} single-line
                # "; "-separated numbered order list (Meta rejects newlines in
                # body PARAMETERS), {{4}} capped-count note (never empty).
                {
                    "type": "body",
                    "parameters": [
                        "store_name",
                        "order_count",
                        "orders_line",
                        "capped_note",
                    ],
                },
                # Quick-reply "All shipped" — payload echoed back to the
                # inbound webhook as ``shipall:<subdomain>/<digest_id>``.
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": ["shipall_payload"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.SHIP_DIGEST,
            name="cod_ship_digest_v1",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "store_name",
                        "order_count",
                        "orders_line",
                        "capped_note",
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": ["shipall_payload"],
                },
            ],
        ),
    },
    MessageType.DELIVERY_CHECK: {
        "en": MessageTemplate(
            type=MessageType.DELIVERY_CHECK,
            name="order_delivery_check_v1",
            language="en_US",
            components=[
                # Body: {{1}} customer name, {{2}} order number, {{3}} store.
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "store_name"],
                },
                # Three quick-reply buttons — payloads carry the action
                # prefix (dlvyes:/dlvnot:/dlvref:) + <subdomain>/<order_id>.
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": ["received_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "1",
                    "parameters": ["notyet_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "2",
                    "parameters": ["refused_payload"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.DELIVERY_CHECK,
            name="order_delivery_check_v1",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "store_name"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": ["received_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "1",
                    "parameters": ["notyet_payload"],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "2",
                    "parameters": ["refused_payload"],
                },
            ],
        ),
    },
    MessageType.OTP_VERIFICATION: {
        # No Meta template submitted — Meta AUTHENTICATION templates use a
        # special OTP component (copy-code / one-tap) that the payload
        # builder cannot emit yet, so a Meta-transport send of this type
        # fails and the feature's `otp_available` capability keeps such
        # stores gated off. GOWA renders the body locally from
        # _PLAIN_ONLY_TEMPLATES (whatsapp_plain_render) and sends it as
        # free text — the only live path in v1.
        "en": MessageTemplate(
            type=MessageType.OTP_VERIFICATION,
            name="otp_verification_v1",
            language="en",
            components=[
                # Body: {{1}} code, {{2}} store name.
                {"type": "body", "parameters": ["code", "store_name"]},
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.OTP_VERIFICATION,
            name="otp_verification_v1",
            language="ar",
            components=[
                {"type": "body", "parameters": ["code", "store_name"]},
            ],
        ),
    },
}


class IMessagingService(ABC):
    """Messaging service interface for customer notifications."""

    @property
    @abstractmethod
    def channel(self) -> MessageChannel:
        """Get the message channel."""
        ...

    @abstractmethod
    async def send_message(
        self,
        content: MessageContent,
    ) -> MessageResult:
        """Send a templated message.

        Args:
            content: Message content with recipient and template params

        Returns:
            MessageResult with delivery status
        """
        ...

    @abstractmethod
    async def send_order_confirmation(
        self,
        recipient: MessageRecipient,
        order_number: str,
        total: str,
        store_name: str,
    ) -> MessageResult:
        """Send order confirmation message.

        Args:
            recipient: Customer contact info
            order_number: Order reference number
            total: Formatted total amount
            store_name: Store name

        Returns:
            MessageResult
        """
        ...

    @abstractmethod
    async def send_shipping_notification(
        self,
        recipient: MessageRecipient,
        order_number: str,
        tracking_number: str,
        carrier: str = "Bosta",
    ) -> MessageResult:
        """Send shipping notification with tracking.

        Args:
            recipient: Customer contact info
            order_number: Order reference number
            tracking_number: Carrier tracking number
            carrier: Shipping carrier name

        Returns:
            MessageResult
        """
        ...

    @abstractmethod
    async def send_delivery_notification(
        self,
        recipient: MessageRecipient,
        order_number: str,
        store_name: str,
    ) -> MessageResult:
        """Send delivery confirmation message.

        Args:
            recipient: Customer contact info
            order_number: Order reference number
            store_name: Store name

        Returns:
            MessageResult
        """
        ...

    @abstractmethod
    async def get_message_status(
        self,
        message_id: str,
    ) -> MessageStatus:
        """Get message delivery status.

        Args:
            message_id: Message ID from send result

        Returns:
            Current message status
        """
        ...

    @abstractmethod
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify incoming webhook signature.

        Args:
            payload: Webhook payload bytes
            signature: Signature header value

        Returns:
            Parsed payload if valid, None if invalid
        """
        ...
