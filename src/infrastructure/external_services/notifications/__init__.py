"""Notification services for configuration events.

This module provides notification capabilities for:
- Email notifications to admins and merchants
- Real-time WebSocket notifications
- In-app notifications
"""

from .notification_service import (
    NotificationService,
    NotificationType,
    NotificationPriority,
    get_notification_service,
)
from .email_templates import (
    ConfigurationRequestEmailTemplate,
    CredentialsConfiguredEmailTemplate,
)

__all__ = [
    "NotificationService",
    "NotificationType",
    "NotificationPriority",
    "get_notification_service",
    "ConfigurationRequestEmailTemplate",
    "CredentialsConfiguredEmailTemplate",
]
