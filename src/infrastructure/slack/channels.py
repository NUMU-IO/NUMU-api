"""Slack channel routing logic.

Routes alerts to appropriate Slack channels based on:
- Alert severity
- Alert service type
- Environment (prod vs non-prod)
"""

from enum import StrEnum

from src.infrastructure.slack.alerts import AlertService, AlertSeverity, SlackAlert


class AlertChannel(StrEnum):
    """Slack channel identifiers.

    Maps to settings.slack_webhook_<channel> environment variables.
    """
    CRITICAL = "critical"   # P0 alerts - page on-call
    PAYMENTS = "payments"   # Payment failures, gateway issues
    FRAUD = "fraud"         # COD fraud detection (restricted)
    SHIPPING = "shipping"   # Bosta failures, SLA breaches
    INFRA = "infra"         # DB, Redis, disk, latency
    BUSINESS = "business"   # GMV anomalies, conversion drops
    DEV = "dev"             # All non-prod alerts


# Channel routing by service type
SERVICE_TO_CHANNEL: dict[AlertService, AlertChannel] = {
    AlertService.PAYMENTS: AlertChannel.PAYMENTS,
    AlertService.FRAUD: AlertChannel.FRAUD,
    AlertService.SHIPPING: AlertChannel.SHIPPING,
    AlertService.INFRASTRUCTURE: AlertChannel.INFRA,
    AlertService.SECURITY: AlertChannel.CRITICAL,  # Security always critical
    AlertService.BUSINESS: AlertChannel.BUSINESS,
}


# Services that should ALSO post to #critical for CRITICAL severity
ESCALATE_TO_CRITICAL: set[AlertService] = {
    AlertService.PAYMENTS,
    AlertService.FRAUD,
    AlertService.INFRASTRUCTURE,
    AlertService.SECURITY,
}


def get_channel_for_alert(alert: SlackAlert) -> AlertChannel:
    """Determine the primary channel for an alert.

    Routing rules:
    1. CRITICAL security alerts -> #critical
    2. Service-specific routing for normal alerts
    3. Fallback to #infra for unknown services
    """
    # Security CRITICAL always goes to critical channel
    if alert.service == AlertService.SECURITY and alert.severity == AlertSeverity.CRITICAL:
        return AlertChannel.CRITICAL

    # Normal service routing
    return SERVICE_TO_CHANNEL.get(alert.service, AlertChannel.INFRA)


def should_escalate_to_critical(alert: SlackAlert) -> bool:
    """Check if alert should ALSO be posted to #critical channel.

    CRITICAL severity alerts from certain services are cross-posted
    to #critical for visibility.
    """
    if alert.severity != AlertSeverity.CRITICAL:
        return False

    return alert.service in ESCALATE_TO_CRITICAL


def get_all_channels_for_alert(alert: SlackAlert) -> list[AlertChannel]:
    """Get all channels an alert should be posted to.

    Returns primary channel + #critical if escalation applies.
    """
    channels = [get_channel_for_alert(alert)]

    # Add critical channel for escalation (avoid duplicates)
    if should_escalate_to_critical(alert):
        if AlertChannel.CRITICAL not in channels:
            channels.append(AlertChannel.CRITICAL)

    return channels


# Channel descriptions for documentation
CHANNEL_DESCRIPTIONS: dict[AlertChannel, str] = {
    AlertChannel.CRITICAL: (
        "#numu-alerts-critical - P0 alerts requiring immediate response. "
        "Payment gateway down, confirmed fraud, security incidents."
    ),
    AlertChannel.PAYMENTS: (
        "#numu-alerts-payments - Payment anomalies, webhook failures, "
        "refund spikes, gateway issues."
    ),
    AlertChannel.FRAUD: (
        "#numu-alerts-fraud - COD fraud detection alerts. "
        "Restricted access - fraud signals should not leak to merchants."
    ),
    AlertChannel.SHIPPING: (
        "#numu-alerts-shipping - Bosta API failures, AWB issues, "
        "delivery SLA breaches."
    ),
    AlertChannel.INFRA: (
        "#numu-alerts-infra - Database latency, Redis unavailable, "
        "disk space warnings, Celery queue backlogs."
    ),
    AlertChannel.BUSINESS: (
        "#numu-alerts-business - GMV anomalies, conversion drops, "
        "business metric alerts."
    ),
    AlertChannel.DEV: (
        "#numu-alerts-dev - All non-production alerts (staging/development). "
        "Single channel for all alert types."
    ),
}
