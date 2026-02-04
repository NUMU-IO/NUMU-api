"""Notification services for configuration events.

This module provides notification capabilities for:
- Email notifications to admins and merchants
- Real-time WebSocket notifications
- In-app notifications
"""

from .email_templates import (
    ConfigurationRequestEmailTemplate,
    CredentialsConfiguredEmailTemplate,
)
from .notification_service import (
    NotificationPriority,
    NotificationService,
    NotificationType,
    get_notification_service,
)

__all__ = [
    "NotificationService",
    "NotificationType",
    "NotificationPriority",
    "get_notification_service",
    "ConfigurationRequestEmailTemplate",
    "CredentialsConfiguredEmailTemplate",
]
