"""Slack alerting module.

This module provides production-grade Slack alerting with:
- Rate limiting and deduplication
- Channel routing by alert type
- Block Kit message formatting
- Environment isolation (prod vs non-prod)
"""

from src.infrastructure.slack.alerts import (
    AlertSeverity,
    SlackAlert,
    create_critical_alert,
    create_fraud_alert,
    create_info_alert,
    create_infrastructure_alert,
    create_payment_alert,
    create_shipping_alert,
    create_warn_alert,
)
from src.infrastructure.slack.channels import AlertChannel, get_channel_for_alert
from src.infrastructure.slack.service import SlackAlertService, slack_alert_service

__all__ = [
    # Alert types
    "AlertSeverity",
    "SlackAlert",
    # Alert builders
    "create_critical_alert",
    "create_warn_alert",
    "create_info_alert",
    "create_payment_alert",
    "create_fraud_alert",
    "create_shipping_alert",
    "create_infrastructure_alert",
    # Channels
    "AlertChannel",
    "get_channel_for_alert",
    # Service
    "SlackAlertService",
    "slack_alert_service",
]
