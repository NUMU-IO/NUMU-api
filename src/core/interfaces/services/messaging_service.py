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
    # Interactive variant — body shows the order + address and asks the
    # customer to tap a single QUICK_REPLY button. Sent in place of
    # ORDER_CONFIRMATION when the store has opted into
    # store.settings.whatsapp_notifications.require_order_confirmation.
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
            name="order_confirmation_v2",
            language="en_US",
            components=[
                # Body: Hi {{1}}, your order {{2}} has been received.
                # Total: {{3}}. Thank you for shopping with us.
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "total"],
                },
                # URL button: Manage order → https://numueg.app/o/{{1}}
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
            name="order_confirmation_v2",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "total"],
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
        # Interactive UTILITY template with one QUICK_REPLY button. The
        # button has no parameters at send time — Meta returns it as
        # `interactive.button_reply.title` on the inbound webhook when
        # the customer taps it, which the order_confirmation webhook
        # handler maps to a "confirmed" state on the order.
        "en": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION_REQUEST,
            name="order_confirmation_request_v1",
            language="en_US",
            components=[
                # Body: Hi {{1}}, your order {{2}} totals {{3}}. Shipping
                # to: {{4}}. Please tap Confirm so we can ship it.
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "total",
                        "shipping_address",
                    ],
                },
                # No "parameters" entry needed for the button — Meta uses
                # the template-side text verbatim; we ignore the button
                # block on send.
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION_REQUEST,
            name="order_confirmation_request_v1",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "total",
                        "shipping_address",
                    ],
                },
            ],
        ),
    },
    MessageType.ORDER_SHIPPED: {
        "en": MessageTemplate(
            type=MessageType.ORDER_SHIPPED,
            name="order_shipped_v2",
            language="en",
            components=[
                # Body: Your order {{1}} is on the way with {{2}}.
                # Thanks for your patience!
                {
                    "type": "body",
                    "parameters": ["order_number", "carrier"],
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
            name="order_shipped_v2",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["order_number", "carrier"],
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
            name="order_delivered",
            language="en",
            components=[
                # Body: Your order {{1}} has been delivered. Thanks for
                # shopping at {{2}}. We hope you enjoy your purchase!
                {
                    "type": "body",
                    "parameters": ["order_number", "store_name"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_DELIVERED,
            name="order_delivered",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["order_number", "store_name"],
                },
            ],
        ),
    },
    MessageType.PAYMENT_RECEIVED: {
        "en": MessageTemplate(
            type=MessageType.PAYMENT_RECEIVED,
            name="payment_received",
            language="en",
            components=[
                # Body: Payment received for order {{1}}. Amount: {{2}}.
                # Thank you!
                {
                    "type": "body",
                    "parameters": ["order_number", "amount"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.PAYMENT_RECEIVED,
            name="payment_received",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["order_number", "amount"],
                },
            ],
        ),
    },
    MessageType.ABANDONED_CART: {
        "en": MessageTemplate(
            type=MessageType.ABANDONED_CART,
            name="abandoned_cart_v2",
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
            name="abandoned_cart_v2",
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
