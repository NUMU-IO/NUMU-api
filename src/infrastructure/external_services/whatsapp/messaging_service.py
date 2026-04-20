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
from typing import TYPE_CHECKING, Any
from uuid import UUID

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

if TYPE_CHECKING:
    from src.infrastructure.repositories.message_log_repository import (
        MessageLogRepository,
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
        self.business_account_id = (
            business_account_id or settings.whatsapp_business_account_id
        )
        self.app_secret = app_secret or settings.whatsapp_app_secret
        self.enabled = settings.whatsapp_enabled
        # Set by get_whatsapp_service() resolver
        self._is_own: bool = False

    @property
    def connection_type(self) -> str:
        """Return 'own' if using per-store credentials, 'shared' otherwise."""
        return "own" if self._is_own else "shared"

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
        cleaned = (
            phone.replace("+", "")
            .replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
        )

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
            {"type": "text", "text": str(value)} for value in parameters.values()
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
                    error_msg = error_data.get("error", {}).get(
                        "message", "Unknown error"
                    )
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

    async def send_and_log(
        self,
        content: MessageContent,
        repo: "MessageLogRepository",
        store_id: UUID,
        tenant_id: UUID | None = None,
    ) -> MessageResult:
        """Send a templated message and persist an outbound MessageLog.

        Wraps ``send_message`` with automatic logging.  If the send
        succeeds, an OUTBOUND log entry is created.  Logging failures
        never break the send flow.

        Args:
            content: Message content with recipient and template params
            repo: MessageLogRepository for persistence
            store_id: Store ID for the log entry
            tenant_id: Tenant ID for the log entry

        Returns:
            MessageResult with delivery status
        """
        result = await self.send_message(content)

        if result.success and result.message_id:
            phone = self._format_phone_number(content.recipient.phone)
            # Resolve template name for the log entry
            language = content.recipient.language
            templates = EGYPTIAN_TEMPLATES.get(content.type, {})
            template = templates.get(language) or templates.get("en")
            template_name = template.name if template else content.type.value

            await self._log_outbound(
                repo,
                store_id=store_id,
                tenant_id=tenant_id,
                phone=phone,
                message_id=result.message_id,
                template_name=template_name,
                content=str(content.template_params),
            )

        return result

    async def send_order_confirmation(
        self,
        recipient: MessageRecipient,
        order_number: str,
        total: str,
        store_name: str,
        tracking_url: str | None = None,
    ) -> MessageResult:
        """Send order confirmation message.

        The WhatsApp template receives ``tracking_url`` as an additional
        parameter. If the template is configured to use it (via a URL
        button or body variable), it'll surface as a tap-to-open tracking
        link — reflecting live status changes from the merchant dashboard.

        Args:
            recipient: Customer contact info
            order_number: Order reference number
            total: Formatted total (e.g., "EGP 250.00")
            store_name: Store name
            tracking_url: Persistent order-tracking URL, e.g.
                ``https://<subdomain>.numueg.app/track/<order_id>``.

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
                "tracking_url": tracking_url or "",
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

    async def send_text_message(
        self,
        phone: str,
        text: str,
    ) -> MessageResult:
        """Send a freeform text message (only within 24h customer service window).

        Args:
            phone: Recipient phone number.
            text: Plain text message body.

        Returns:
            MessageResult with delivery status.
        """
        if not self.enabled:
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message="WhatsApp messaging is disabled",
            )

        formatted_phone = self._format_phone_number(phone)
        payload = {
            "messaging_product": "whatsapp",
            "to": formatted_phone,
            "type": "text",
            "text": {"body": text},
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
                    return MessageResult(
                        success=True,
                        message_id=message_id,
                        channel=MessageChannel.WHATSAPP,
                        status=MessageStatus.SENT,
                    )
                else:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get(
                        "message", "Unknown error"
                    )
                    return MessageResult(
                        success=False,
                        channel=MessageChannel.WHATSAPP,
                        status=MessageStatus.FAILED,
                        error_message=error_msg,
                    )
        except Exception as e:
            logger.error(f"WhatsApp text send error: {e}")
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message=str(e),
            )

    async def send_media_message(
        self,
        phone: str,
        media_url: str,
        caption: str | None = None,
        media_type: str = "image",
    ) -> MessageResult:
        """Send a media message (image, document, video).

        Args:
            phone: Recipient phone number.
            media_url: Public URL of the media file.
            caption: Optional caption text.
            media_type: One of 'image', 'document', 'video'.

        Returns:
            MessageResult with delivery status.
        """
        if not self.enabled:
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message="WhatsApp messaging is disabled",
            )

        formatted_phone = self._format_phone_number(phone)
        media_obj: dict[str, Any] = {"link": media_url}
        if caption:
            media_obj["caption"] = caption

        payload = {
            "messaging_product": "whatsapp",
            "to": formatted_phone,
            "type": media_type,
            media_type: media_obj,
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
                    return MessageResult(
                        success=True,
                        message_id=message_id,
                        channel=MessageChannel.WHATSAPP,
                        status=MessageStatus.SENT,
                    )
                else:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get(
                        "message", "Unknown error"
                    )
                    return MessageResult(
                        success=False,
                        channel=MessageChannel.WHATSAPP,
                        status=MessageStatus.FAILED,
                        error_message=error_msg,
                    )
        except Exception as e:
            logger.error(f"WhatsApp media send error: {e}")
            return MessageResult(
                success=False,
                channel=MessageChannel.WHATSAPP,
                status=MessageStatus.FAILED,
                error_message=str(e),
            )

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
        logger.info(
            f"Status check for message {message_id} - use webhooks for real status"
        )
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

    async def _log_outbound(
        self,
        repo: "MessageLogRepository",
        *,
        store_id: UUID,
        tenant_id: UUID | None,
        phone: str,
        message_id: str,
        template_name: str,
        content: str,
    ) -> None:
        """Persist an outbound message log entry."""
        from src.core.entities.message_log import (
            MessageDirection,
            MessageLog,
        )
        from src.core.entities.message_log import (
            MessageStatus as LogStatus,
        )

        try:
            log_entry = MessageLog(
                tenant_id=tenant_id,
                store_id=store_id,
                phone=phone,
                message_id=message_id,
                direction=MessageDirection.OUTBOUND,
                template_name=template_name,
                content=content,
                status=LogStatus.SENT,
            )
            await repo.create(log_entry)
            logger.info(f"Outbound message logged: {message_id}")
        except Exception as e:
            # Never let logging break the send flow
            logger.error(f"Failed to log outbound message: {e}")

    @staticmethod
    def _extract_error_code(status_update: dict) -> str | None:
        """Extract the first error code from a WhatsApp status update."""
        errors = status_update.get("errors", [])
        if errors:
            return str(errors[0].get("code", ""))
        return None

    async def handle_webhook_event(
        self,
        data: dict,
        message_log_repo: "MessageLogRepository | None" = None,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> None:
        """Process a verified webhook event.

        Args:
            data: Parsed webhook payload
            message_log_repo: Optional repo to persist status updates / inbound logs.
            store_id: Store ID for inbound log entries (resolved from prior
                messages when not provided).
            tenant_id: Tenant ID for inbound log entries (resolved from prior
                messages when not provided).
        """
        from src.core.entities.message_log import (
            MessageDirection,
            MessageLog,
        )
        from src.core.entities.message_log import (
            MessageStatus as LogStatus,
        )

        log_status_map = {
            "sent": LogStatus.SENT,
            "delivered": LogStatus.DELIVERED,
            "read": LogStatus.READ,
            "failed": LogStatus.FAILED,
        }

        entries = data.get("entry", [])

        for entry in entries:
            changes = entry.get("changes", [])

            for change in changes:
                value = change.get("value", {})

                # ── Status updates (sent / delivered / read / failed) ──
                statuses = value.get("statuses", [])
                for status_update in statuses:
                    wa_message_id = status_update.get("id")
                    status_value = status_update.get("status")
                    recipient_id = status_update.get("recipient_id")

                    logger.info(
                        f"WhatsApp status update: {wa_message_id} -> {status_value} "
                        f"for {recipient_id}"
                    )

                    if message_log_repo and wa_message_id:
                        mapped = log_status_map.get(status_value)
                        error_code = self._extract_error_code(status_update)
                        if mapped:
                            try:
                                await message_log_repo.update_status(
                                    message_id=wa_message_id,
                                    status=mapped,
                                    error_code=error_code
                                    if status_value == "failed"
                                    else None,
                                )
                            except Exception as e:
                                logger.error(f"Failed to update message status: {e}")

                    if status_value == "failed":
                        for error in status_update.get("errors", []):
                            logger.error(
                                f"WhatsApp message failed: {wa_message_id}, "
                                f"code={error.get('code')}, "
                                f"title={error.get('title')}, "
                                f"message={error.get('message')}"
                            )

                # ── Incoming customer messages ──
                messages = value.get("messages", [])
                for message in messages:
                    from_number = message.get("from")
                    msg_id = message.get("id")
                    msg_type = message.get("type")

                    # Extract text content where available
                    text_content: str | None = None
                    if msg_type == "text":
                        text_content = message.get("text", {}).get("body", "")
                    elif msg_type == "button":
                        text_content = message.get("button", {}).get("text", "")
                    elif msg_type == "interactive":
                        text_content = str(
                            message.get("interactive", {}).get("type", "")
                        )

                    logger.info(
                        f"WhatsApp incoming message from {from_number}: "
                        f"type={msg_type}, id={msg_id}"
                    )

                    if not (message_log_repo and msg_id and from_number):
                        continue

                    # Resolve store/tenant context from prior messages
                    # when the caller did not provide them.
                    resolved_store = store_id
                    resolved_tenant = tenant_id
                    if not resolved_store:
                        try:
                            prior = await message_log_repo.get_latest_by_phone(
                                from_number
                            )
                            if prior:
                                resolved_store = prior.store_id
                                resolved_tenant = prior.tenant_id
                            else:
                                logger.warning(
                                    f"Cannot resolve store for inbound "
                                    f"from {from_number} — no prior messages"
                                )
                                continue
                        except Exception as e:
                            logger.error(f"Failed to resolve store context: {e}")
                            continue

                    try:
                        inbound = MessageLog(
                            tenant_id=resolved_tenant,
                            store_id=resolved_store,
                            phone=from_number,
                            message_id=msg_id,
                            direction=MessageDirection.INBOUND,
                            content=text_content,
                            status=LogStatus.DELIVERED,
                            metadata={"type": msg_type},
                        )
                        await message_log_repo.create(inbound)
                        logger.info(f"Inbound message logged: {msg_id}")
                    except Exception as e:
                        logger.error(f"Failed to log inbound message: {e}")
