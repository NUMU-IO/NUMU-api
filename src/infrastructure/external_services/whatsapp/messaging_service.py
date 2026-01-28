"""WhatsApp Business API messaging service implementation.

Integrates with WhatsApp Business API (Cloud API) for sending
templated messages to customers. Commonly used for:
- Order confirmations
- Shipping notifications
- Delivery updates
- Payment confirmations

API Documentation: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

import hashlib
import hmac
import logging
from typing import Any

import httpx

from src.config import settings
from src.core.interfaces.services.messaging_service import (
    EGYPTIAN_TEMPLATES,
    IMessagingService,
    MessageChannel,
    MessageContent,
    MessageRecipient,
    MessageResult,
    MessageStatus,
    MessageType,
)

logger = logging.getLogger(__name__)

# WhatsApp Cloud API base URL
WHATSAPP_API_BASE = "https://graph.facebook.com/v18.0"


class WhatsAppMessagingService(IMessagingService):
    """WhatsApp Business API messaging service.

    Uses the Cloud API to send templated messages. All messages must
    use pre-approved templates registered with WhatsApp Business.

    Flow:
    1. Register message templates with WhatsApp Business
    2. Call send_message() with template params
    3. WhatsApp delivers to customer
    4. Webhook receives delivery status updates
    """

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        business_account_id: str | None = None,
        app_secret: str | None = None,
    ) -> None:
        self.access_token = access_token or settings.whatsapp_access_token
        self.phone_number_id = phone_number_id or settings.whatsapp_phone_number_id
        self.business_account_id = business_account_id or settings.whatsapp_business_account_id
        self.app_secret = app_secret or settings.whatsapp_app_secret
        self.enabled = settings.whatsapp_enabled

    @property
    def channel(self) -> MessageChannel:
        """Get the message channel."""
        return MessageChannel.WHATSAPP

    def _get_headers(self) -> dict[str, str]:
        """Get API request headers."""
        if not self.access_token:
            raise ValueError("WhatsApp access token not configured")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _format_phone_number(self, phone: str) -> str:
        """Format phone number for WhatsApp API.

        WhatsApp requires numbers in international format without + or spaces.

        Args:
            phone: Phone number (various formats accepted)

        Returns:
            Formatted phone number (e.g., "201234567890")
        """
        # Remove common separators and prefixes
        cleaned = phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

        # Handle Egyptian numbers without country code
        if cleaned.startswith("0") and len(cleaned) == 11:
            cleaned = "2" + cleaned  # Add Egypt country code

        return cleaned

    def _build_template_message(
        self,
        template_name: str,
        language: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Build WhatsApp template message payload.

        Args:
            template_name: Name of approved template
            language: Language code (en, ar)
            parameters: Template parameter values

        Returns:
            Template message component dict
        """
        # Convert parameters dict to ordered list for body component
        body_params = [
            {"type": "text", "text": str(value)}
            for value in parameters.values()
        ]

        return {
            "name": template_name,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": body_params,
                }
            ],
        }

    async def send_message(
        self,
        content: MessageContent,
    ) -> MessageResult:
        """Send a templated message via WhatsApp.

        Args:
            content: Message content with recipient and template params

        Returns:
            MessageResult with delivery status
        """
        if not self.enabled:
            logger.warning("WhatsApp messaging is disabled")
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message="WhatsApp messaging is disabled",
            )

        if not self.access_token or not self.phone_number_id:
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message="WhatsApp credentials not configured",
            )

        # Get template for message type and language
        language = content.recipient.language
        templates = EGYPTIAN_TEMPLATES.get(content.type, {})
        template = templates.get(language) or templates.get("en")

        if not template:
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message=f"No template found for {content.type}",
            )

        # Build message payload
        phone = self._format_phone_number(content.recipient.phone)
        template_message = self._build_template_message(
            template.name,
            template.language,
            content.template_params,
        )

        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": template_message,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{WHATSAPP_API_BASE}/{self.phone_number_id}/messages",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=30.0,
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    message_id = data.get("messages", [{}])[0].get("id")

                    logger.info(f"WhatsApp message sent: {message_id}")
                    return MessageResult(
                        success=True,
                        message_id=message_id,
                        channel=MessageChannel.WHATSAPP,
                        status=MessageStatus.SENT,
                    )
                else:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", "Unknown error")
                    error_code = error_data.get("error", {}).get("code")

                    logger.error(f"WhatsApp send failed: {error_msg}")
                    return MessageResult(
                        success=False,
                        channel=MessageChannel.WHATSAPP,
                        status=MessageStatus.FAILED,
                        error_message=error_msg,
                        error_code=str(error_code) if error_code else None,
                    )

        except Exception as e:
            logger.error(f"WhatsApp send error: {e}")
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message=str(e),
            )

    async def send_order_confirmation(
        self,
        recipient: MessageRecipient,
        order_number: str,
        total: str,
        store_name: str,
    ) -> MessageResult:
        """Send order confirmation message.

        Example message (English):
        "Hi {name}! Your order #{order_number} for {total} from {store_name}
        has been confirmed. We'll notify you when it ships. شكراً لك!"

        Args:
            recipient: Customer contact info
            order_number: Order reference number
            total: Formatted total (e.g., "EGP 250.00")
            store_name: Store name

        Returns:
            MessageResult
        """
        content = MessageContent(
            type=MessageType.ORDER_CONFIRMATION,
            recipient=recipient,
            template_params={
                "customer_name": recipient.name or "Customer",
                "order_number": order_number,
                "total": total,
                "store_name": store_name,
            },
        )
        return await self.send_message(content)

    async def send_shipping_notification(
        self,
        recipient: MessageRecipient,
        order_number: str,
        tracking_number: str,
        carrier: str = "Bosta",
    ) -> MessageResult:
        """Send shipping notification with tracking.

        Example message (Arabic):
        "مرحباً {name}! تم شحن طلبك #{order_number}.
        رقم التتبع: {tracking_number}
        شركة الشحن: {carrier}"

        Args:
            recipient: Customer contact info
            order_number: Order reference number
            tracking_number: Carrier tracking number
            carrier: Shipping carrier name

        Returns:
            MessageResult
        """
        content = MessageContent(
            type=MessageType.ORDER_SHIPPED,
            recipient=recipient,
            template_params={
                "customer_name": recipient.name or "Customer",
                "order_number": order_number,
                "tracking_number": tracking_number,
                "carrier": carrier,
            },
        )
        return await self.send_message(content)

    async def send_out_for_delivery(
        self,
        recipient: MessageRecipient,
        order_number: str,
    ) -> MessageResult:
        """Send out for delivery notification.

        Args:
            recipient: Customer contact info
            order_number: Order reference number

        Returns:
            MessageResult
        """
        content = MessageContent(
            type=MessageType.OUT_FOR_DELIVERY,
            recipient=recipient,
            template_params={
                "customer_name": recipient.name or "Customer",
                "order_number": order_number,
            },
        )
        return await self.send_message(content)

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
        content = MessageContent(
            type=MessageType.ORDER_DELIVERED,
            recipient=recipient,
            template_params={
                "customer_name": recipient.name or "Customer",
                "order_number": order_number,
                "store_name": store_name,
            },
        )
        return await self.send_message(content)

    async def send_payment_received(
        self,
        recipient: MessageRecipient,
        order_number: str,
        amount: str,
    ) -> MessageResult:
        """Send payment confirmation message.

        Args:
            recipient: Customer contact info
            order_number: Order reference number
            amount: Formatted payment amount

        Returns:
            MessageResult
        """
        content = MessageContent(
            type=MessageType.PAYMENT_RECEIVED,
            recipient=recipient,
            template_params={
                "customer_name": recipient.name or "Customer",
                "amount": amount,
                "order_number": order_number,
            },
        )
        return await self.send_message(content)

    async def get_message_status(
        self,
        message_id: str,
    ) -> MessageStatus:
        """Get message delivery status from WhatsApp.

        Note: Status is typically received via webhooks, not polling.
        This method is for occasional status checks.

        Args:
            message_id: WhatsApp message ID

        Returns:
            Current message status
        """
        # WhatsApp doesn't have a direct status check API
        # Status is delivered via webhooks
        # This is a placeholder that returns SENT
        logger.info(f"Status check for message {message_id} - use webhooks for real status")
        return MessageStatus.SENT

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify WhatsApp webhook signature.

        WhatsApp uses HMAC SHA-256 with the app secret.

        Args:
            payload: Webhook payload bytes
            signature: X-Hub-Signature-256 header value

        Returns:
            Parsed payload if valid, None if invalid
        """
        if not self.app_secret:
            logger.warning("WhatsApp app secret not configured")
            return None

        try:
            # Signature format: "sha256=<hash>"
            if signature.startswith("sha256="):
                signature = signature[7:]

            expected_signature = hmac.new(
                self.app_secret.encode(),
                payload,
                hashlib.sha256,
            ).hexdigest()

            if hmac.compare_digest(expected_signature, signature):
                import json
                return json.loads(payload)
            else:
                logger.warning("WhatsApp webhook signature mismatch")
                return None

        except Exception as e:
            logger.error(f"WhatsApp webhook verification error: {e}")
            return None

    def handle_webhook_event(self, data: dict) -> None:
        """Process a verified webhook event.

        Args:
            data: Parsed webhook payload
        """
        # Extract entry and changes
        entries = data.get("entry", [])

        for entry in entries:
            changes = entry.get("changes", [])

            for change in changes:
                value = change.get("value", {})

                # Handle message status updates
                statuses = value.get("statuses", [])
                for status in statuses:
                    message_id = status.get("id")
                    status_value = status.get("status")
                    recipient_id = status.get("recipient_id")

                    logger.info(
                        f"WhatsApp status update: {message_id} -> {status_value} "
                        f"for {recipient_id}"
                    )

                    # TODO: Update message status in database
                    # await message_repository.update_status(message_id, status_value)

                # Handle incoming messages (if supporting two-way chat)
                messages = value.get("messages", [])
                for message in messages:
                    from_number = message.get("from")
                    msg_type = message.get("type")
                    msg_id = message.get("id")

                    logger.info(
                        f"WhatsApp incoming message from {from_number}: "
                        f"type={msg_type}, id={msg_id}"
                    )

                    # TODO: Handle incoming messages (customer replies)
                    # This could trigger customer service workflows
