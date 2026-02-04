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


# Predefined templates for Egyptian market
# These correspond to approved WhatsApp Business templates
EGYPTIAN_TEMPLATES = {
    MessageType.ORDER_CONFIRMATION: {
        "en": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION,
            name="order_confirmation_en",
            language="en",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "total",
                        "store_name",
                    ],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_CONFIRMATION,
            name="order_confirmation_ar",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "total",
                        "store_name",
                    ],
                },
            ],
        ),
    },
    MessageType.ORDER_SHIPPED: {
        "en": MessageTemplate(
            type=MessageType.ORDER_SHIPPED,
            name="order_shipped_en",
            language="en",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "tracking_number",
                        "carrier",
                    ],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_SHIPPED,
            name="order_shipped_ar",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        "customer_name",
                        "order_number",
                        "tracking_number",
                        "carrier",
                    ],
                },
            ],
        ),
    },
    MessageType.OUT_FOR_DELIVERY: {
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
            name="order_delivered_en",
            language="en",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "order_number", "store_name"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.ORDER_DELIVERED,
            name="order_delivered_ar",
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
            name="payment_received_en",
            language="en",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "amount", "order_number"],
                },
            ],
        ),
        "ar": MessageTemplate(
            type=MessageType.PAYMENT_RECEIVED,
            name="payment_received_ar",
            language="ar",
            components=[
                {
                    "type": "body",
                    "parameters": ["customer_name", "amount", "order_number"],
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
