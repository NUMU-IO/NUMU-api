"""Notification service for configuration events.

This service handles sending notifications via multiple channels:
- Email (via SMTP or SendGrid/Mailgun)
- Real-time WebSocket (via Redis pub/sub)
- In-app notifications (stored in database)
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

import httpx

from src.config.settings import settings

logger = logging.getLogger(__name__)


class NotificationType(str, Enum):
    """Types of notifications."""
    # Configuration request notifications
    CONFIG_REQUEST_CREATED = "config_request_created"
    CONFIG_REQUEST_UPDATED = "config_request_updated"
    CONFIG_REQUEST_COMPLETED = "config_request_completed"
    CONFIG_REQUEST_REJECTED = "config_request_rejected"

    # Credential notifications
    CREDENTIALS_CONFIGURED = "credentials_configured"
    CREDENTIALS_VALIDATED = "credentials_validated"
    CREDENTIALS_VALIDATION_FAILED = "credentials_validation_failed"
    CREDENTIALS_REVOKED = "credentials_revoked"


class NotificationPriority(str, Enum):
    """Priority levels for notifications."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class NotificationPayload:
    """Payload for a notification."""
    type: NotificationType
    priority: NotificationPriority
    recipient_id: UUID
    recipient_email: str | None
    title: str
    message: str
    data: dict[str, Any]
    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "type": self.type.value,
            "priority": self.priority.value,
            "recipient_id": str(self.recipient_id),
            "recipient_email": self.recipient_email,
            "title": self.title,
            "message": self.message,
            "data": self.data,
            "created_at": self.created_at.isoformat(),
        }


class NotificationService:
    """Service for sending notifications via multiple channels.

    This service supports:
    - Email notifications (SMTP, SendGrid, Mailgun)
    - Real-time WebSocket notifications (via Redis pub/sub)
    - In-app notifications (stored in database)

    Usage:
        service = NotificationService()
        await service.notify_admin_new_request(request)
        await service.notify_merchant_credentials_configured(credential)
    """

    def __init__(
        self,
        redis_client = None,
        db_session = None,
    ):
        self.redis_client = redis_client
        self.db_session = db_session
        self._email_provider = settings.EMAIL_PROVIDER if hasattr(settings, 'EMAIL_PROVIDER') else "smtp"

    async def send_notification(
        self,
        payload: NotificationPayload,
        channels: list[str] = None,
    ) -> bool:
        """Send a notification via specified channels.

        Args:
            payload: The notification payload
            channels: List of channels ("email", "websocket", "in_app")
                     Defaults to all channels if not specified

        Returns:
            True if notification was sent successfully
        """
        if channels is None:
            channels = ["email", "websocket", "in_app"]

        success = True

        for channel in channels:
            try:
                if channel == "email" and payload.recipient_email:
                    await self._send_email(payload)
                elif channel == "websocket":
                    await self._send_websocket(payload)
                elif channel == "in_app":
                    await self._store_in_app(payload)
            except Exception as e:
                logger.error(f"Failed to send notification via {channel}: {e}")
                success = False

        return success

    async def notify_admin_new_request(
        self,
        request_id: UUID,
        tenant_id: UUID,
        tenant_name: str,
        service_type: str,
        service_name: str,
        priority: str,
        admin_emails: list[str],
    ) -> None:
        """Notify admins about a new configuration request.

        Args:
            request_id: The configuration request ID
            tenant_id: The tenant/merchant ID
            tenant_name: The merchant's business name
            service_type: Type of service requested
            service_name: Specific service provider
            priority: Request priority level
            admin_emails: List of admin email addresses
        """
        for admin_email in admin_emails:
            payload = NotificationPayload(
                type=NotificationType.CONFIG_REQUEST_CREATED,
                priority=NotificationPriority.HIGH if priority == "urgent" else NotificationPriority.NORMAL,
                recipient_id=UUID("00000000-0000-0000-0000-000000000000"),  # System admin
                recipient_email=admin_email,
                title=f"New Configuration Request: {service_name}",
                message=f"Merchant '{tenant_name}' has requested configuration for {service_name} ({service_type}). Priority: {priority}",
                data={
                    "request_id": str(request_id),
                    "tenant_id": str(tenant_id),
                    "tenant_name": tenant_name,
                    "service_type": service_type,
                    "service_name": service_name,
                    "priority": priority,
                    "action_url": f"/admin/credentials/requests/{request_id}",
                }
            )
            await self.send_notification(payload)

    async def notify_merchant_credentials_configured(
        self,
        tenant_id: UUID,
        merchant_email: str,
        merchant_name: str,
        service_type: str,
        service_name: str,
    ) -> None:
        """Notify merchant that credentials have been configured.

        Args:
            tenant_id: The tenant/merchant ID
            merchant_email: Merchant's email address
            merchant_name: Merchant's business name
            service_type: Type of service configured
            service_name: Specific service provider
        """
        payload = NotificationPayload(
            type=NotificationType.CREDENTIALS_CONFIGURED,
            priority=NotificationPriority.HIGH,
            recipient_id=tenant_id,
            recipient_email=merchant_email,
            title=f"{service_name} is Now Active!",
            message=f"Great news! Your {service_name} integration has been configured and is ready to use.",
            data={
                "tenant_id": str(tenant_id),
                "service_type": service_type,
                "service_name": service_name,
                "action_url": f"/settings/{service_type}",
            }
        )
        await self.send_notification(payload)

    async def notify_merchant_request_rejected(
        self,
        tenant_id: UUID,
        merchant_email: str,
        service_name: str,
        reason: str,
    ) -> None:
        """Notify merchant that their request was rejected.

        Args:
            tenant_id: The tenant/merchant ID
            merchant_email: Merchant's email address
            service_name: Specific service provider
            reason: Reason for rejection
        """
        payload = NotificationPayload(
            type=NotificationType.CONFIG_REQUEST_REJECTED,
            priority=NotificationPriority.NORMAL,
            recipient_id=tenant_id,
            recipient_email=merchant_email,
            title=f"Configuration Request Update: {service_name}",
            message=f"Your configuration request for {service_name} could not be completed. Reason: {reason}",
            data={
                "tenant_id": str(tenant_id),
                "service_name": service_name,
                "reason": reason,
                "action_url": "/settings/configuration-requests",
            }
        )
        await self.send_notification(payload)

    async def _send_email(self, payload: NotificationPayload) -> None:
        """Send email notification.

        Args:
            payload: The notification payload
        """
        if self._email_provider == "sendgrid":
            await self._send_via_sendgrid(payload)
        elif self._email_provider == "mailgun":
            await self._send_via_mailgun(payload)
        else:
            await self._send_via_smtp(payload)

    async def _send_via_smtp(self, payload: NotificationPayload) -> None:
        """Send email via SMTP.

        Args:
            payload: The notification payload
        """
        # Implementation would use aiosmtplib
        logger.info(f"[SMTP] Sending email to {payload.recipient_email}: {payload.title}")
        # TODO: Implement actual SMTP sending

    async def _send_via_sendgrid(self, payload: NotificationPayload) -> None:
        """Send email via SendGrid.

        Args:
            payload: The notification payload
        """
        api_key = getattr(settings, 'SENDGRID_API_KEY', None)
        if not api_key:
            logger.warning("SendGrid API key not configured")
            return

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{
                        "to": [{"email": payload.recipient_email}],
                    }],
                    "from": {"email": "noreply@numu.io", "name": "NUMU"},
                    "subject": payload.title,
                    "content": [{
                        "type": "text/html",
                        "value": self._build_email_html(payload),
                    }],
                }
            )

            if response.status_code not in [200, 202]:
                logger.error(f"SendGrid error: {response.text}")

    async def _send_via_mailgun(self, payload: NotificationPayload) -> None:
        """Send email via Mailgun.

        Args:
            payload: The notification payload
        """
        api_key = getattr(settings, 'MAILGUN_API_KEY', None)
        domain = getattr(settings, 'MAILGUN_DOMAIN', None)

        if not api_key or not domain:
            logger.warning("Mailgun not configured")
            return

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.mailgun.net/v3/{domain}/messages",
                auth=("api", api_key),
                data={
                    "from": f"NUMU <noreply@{domain}>",
                    "to": payload.recipient_email,
                    "subject": payload.title,
                    "html": self._build_email_html(payload),
                }
            )

            if response.status_code != 200:
                logger.error(f"Mailgun error: {response.text}")

    async def _send_websocket(self, payload: NotificationPayload) -> None:
        """Send real-time notification via WebSocket (Redis pub/sub).

        Args:
            payload: The notification payload
        """
        if not self.redis_client:
            logger.debug("Redis client not available for WebSocket notification")
            return

        channel = f"notifications:{payload.recipient_id}"
        await self.redis_client.publish(channel, json.dumps(payload.to_dict()))
        logger.debug(f"Published notification to channel: {channel}")

    async def _store_in_app(self, payload: NotificationPayload) -> None:
        """Store notification in database for in-app display.

        Args:
            payload: The notification payload
        """
        if not self.db_session:
            logger.debug("Database session not available for in-app notification")
            return

        # TODO: Create InAppNotification model and store
        logger.debug(f"Stored in-app notification for user: {payload.recipient_id}")

    def _build_email_html(self, payload: NotificationPayload) -> str:
        """Build HTML email content.

        Args:
            payload: The notification payload

        Returns:
            HTML string for email body
        """
        action_url = payload.data.get("action_url", "")
        base_url = getattr(settings, 'FRONTEND_URL', 'https://dashboard.numu.io')
        full_action_url = f"{base_url}{action_url}"

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #D4AF37, #1034A6); padding: 20px; text-align: center; }}
                .header h1 {{ color: white; margin: 0; }}
                .content {{ padding: 30px; background: #f9f9f9; }}
                .button {{ display: inline-block; padding: 12px 24px; background: #D4AF37; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }}
                .footer {{ padding: 20px; text-align: center; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>NUMU</h1>
                </div>
                <div class="content">
                    <h2>{payload.title}</h2>
                    <p>{payload.message}</p>
                    {f'<a href="{full_action_url}" class="button">View Details</a>' if action_url else ''}
                </div>
                <div class="footer">
                    <p>© 2026 NUMU. All rights reserved.</p>
                    <p>Egyptian Marketplace Platform</p>
                </div>
            </div>
        </body>
        </html>
        """


# Singleton instance
_notification_service: NotificationService | None = None


def get_notification_service(
    redis_client = None,
    db_session = None,
) -> NotificationService:
    """Get or create the notification service instance.

    Args:
        redis_client: Optional Redis client for WebSocket notifications
        db_session: Optional database session for in-app notifications

    Returns:
        NotificationService instance
    """
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService(redis_client, db_session)
    return _notification_service
