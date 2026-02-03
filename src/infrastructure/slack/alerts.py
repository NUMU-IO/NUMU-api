"""Slack alert dataclasses and builders.

Defines the standard alert format for NUMU's Slack notifications.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class AlertSeverity(str, Enum):
    """Alert severity levels with response time expectations.

    CRITICAL: < 15 min response, auto-escalate to on-call
    WARN: < 2 hours response, requires attention
    INFO: Next business day, informational only
    """
    CRITICAL = "CRITICAL"
    WARN = "WARN"
    INFO = "INFO"


class AlertService(str, Enum):
    """Service categories for alerts."""
    PAYMENTS = "payments"
    FRAUD = "fraud"
    SHIPPING = "shipping"
    INFRASTRUCTURE = "infra"
    SECURITY = "security"
    BUSINESS = "business"


@dataclass
class AlertAction:
    """Interactive button for Slack alert."""
    text: str
    url: str | None = None
    action_id: str | None = None  # For interactive callbacks
    style: str | None = None  # "primary" or "danger"
    value: str | None = None  # Callback value


@dataclass
class SlackAlert:
    """Standard Slack alert message.

    Required fields:
    - severity: CRITICAL, WARN, or INFO
    - title: Action-oriented description (max 100 chars)
    - service: Which service generated the alert

    Optional context fields:
    - tenant_id: Multi-tenant identifier
    - order_id: Related order
    - user_id: Slack user ID for mentions
    - amount: Financial amount (for payment alerts)
    - correlation_id: Links to logs/traces
    """
    # Required fields
    severity: AlertSeverity
    title: str
    service: AlertService

    # Auto-generated
    timestamp: datetime = field(default_factory=datetime.utcnow)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Optional context
    tenant_id: str | None = None
    order_id: str | None = None
    customer_phone: str | None = None
    amount: Decimal | None = None
    currency: str = "EGP"

    # Slack-specific
    mention_users: list[str] = field(default_factory=list)  # Slack user IDs
    notes: str | None = None  # Additional context, can include @mentions

    # Extended details
    details: dict[str, Any] = field(default_factory=dict)
    risk_signals: list[str] = field(default_factory=list)  # For fraud alerts

    # Actions (buttons)
    actions: list[AlertAction] = field(default_factory=list)

    # Sentry/logging links
    sentry_url: str | None = None
    runbook_url: str | None = None
    dashboard_url: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize alert data."""
        # Truncate title if too long
        if len(self.title) > 100:
            self.title = self.title[:97] + "..."

        # Ensure severity is enum
        if isinstance(self.severity, str):
            self.severity = AlertSeverity(self.severity)

        # Ensure service is enum
        if isinstance(self.service, str):
            self.service = AlertService(self.service)

    @property
    def dedup_key(self) -> str:
        """Generate deduplication key for rate limiting.

        Same alert type + same context = same key.
        """
        key_parts = [
            self.service.value,
            self.title,
            self.tenant_id or "global",
        ]
        key_string = ":".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()[:16]

    @property
    def severity_emoji(self) -> str:
        """Get emoji for severity level."""
        return {
            AlertSeverity.CRITICAL: "\U0001F534",  # Red circle
            AlertSeverity.WARN: "\U0001F7E1",      # Yellow circle
            AlertSeverity.INFO: "\U0001F535",      # Blue circle
        }[self.severity]

    def with_suppressed_count(self, count: int) -> "SlackAlert":
        """Return a copy with suppressed count in title."""
        if count > 0:
            new_alert = SlackAlert(
                severity=self.severity,
                title=f"{self.title} (+{count} similar)",
                service=self.service,
                timestamp=self.timestamp,
                correlation_id=self.correlation_id,
                tenant_id=self.tenant_id,
                order_id=self.order_id,
                customer_phone=self.customer_phone,
                amount=self.amount,
                currency=self.currency,
                mention_users=self.mention_users,
                notes=self.notes,
                details=self.details,
                risk_signals=self.risk_signals,
                actions=self.actions,
                sentry_url=self.sentry_url,
                runbook_url=self.runbook_url,
                dashboard_url=self.dashboard_url,
            )
            return new_alert
        return self


# ============================================================================
# Alert Builder Functions
# ============================================================================

def create_critical_alert(
    title: str,
    service: AlertService | str,
    *,
    tenant_id: str | None = None,
    correlation_id: str | None = None,
    mention_users: list[str] | None = None,
    details: dict[str, Any] | None = None,
    runbook_url: str | None = None,
    sentry_url: str | None = None,
) -> SlackAlert:
    """Create a CRITICAL severity alert.

    Use for: System down, payment gateway failure, confirmed fraud.
    Expected response: < 15 minutes.
    """
    return SlackAlert(
        severity=AlertSeverity.CRITICAL,
        title=title,
        service=AlertService(service) if isinstance(service, str) else service,
        tenant_id=tenant_id,
        correlation_id=correlation_id or uuid.uuid4().hex[:12],
        mention_users=mention_users or [],
        details=details or {},
        runbook_url=runbook_url,
        sentry_url=sentry_url,
    )


def create_warn_alert(
    title: str,
    service: AlertService | str,
    *,
    tenant_id: str | None = None,
    correlation_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SlackAlert:
    """Create a WARN severity alert.

    Use for: High fraud score, refund spike, SLA breach.
    Expected response: < 2 hours.
    """
    return SlackAlert(
        severity=AlertSeverity.WARN,
        title=title,
        service=AlertService(service) if isinstance(service, str) else service,
        tenant_id=tenant_id,
        correlation_id=correlation_id or uuid.uuid4().hex[:12],
        details=details or {},
    )


def create_info_alert(
    title: str,
    service: AlertService | str,
    *,
    tenant_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SlackAlert:
    """Create an INFO severity alert.

    Use for: Daily summaries, threshold approaching.
    Expected response: Next business day.
    """
    return SlackAlert(
        severity=AlertSeverity.INFO,
        title=title,
        service=AlertService(service) if isinstance(service, str) else service,
        tenant_id=tenant_id,
        details=details or {},
    )


def create_payment_alert(
    title: str,
    severity: AlertSeverity | str,
    *,
    tenant_id: str | None = None,
    order_id: str | None = None,
    amount: Decimal | None = None,
    currency: str = "EGP",
    gateway: str | None = None,
    correlation_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SlackAlert:
    """Create a payment-related alert.

    Use for: Gateway failures, webhook timeouts, refund spikes.
    """
    alert_details = details or {}
    if gateway:
        alert_details["gateway"] = gateway

    return SlackAlert(
        severity=AlertSeverity(severity) if isinstance(severity, str) else severity,
        title=f"[PAYMENTS] {title}",
        service=AlertService.PAYMENTS,
        tenant_id=tenant_id,
        order_id=order_id,
        amount=amount,
        currency=currency,
        correlation_id=correlation_id or uuid.uuid4().hex[:12],
        details=alert_details,
    )


def create_fraud_alert(
    title: str,
    severity: AlertSeverity | str,
    *,
    tenant_id: str | None = None,
    order_id: str | None = None,
    amount: Decimal | None = None,
    fraud_score: float | None = None,
    customer_phone: str | None = None,
    risk_signals: list[str] | None = None,
    correlation_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SlackAlert:
    """Create a fraud detection alert.

    Use for: High fraud scores, suspicious patterns, confirmed fraud.
    """
    alert_details = details or {}
    if fraud_score is not None:
        alert_details["fraud_score"] = f"{fraud_score:.2f}"

    return SlackAlert(
        severity=AlertSeverity(severity) if isinstance(severity, str) else severity,
        title=f"[FRAUD] {title}",
        service=AlertService.FRAUD,
        tenant_id=tenant_id,
        order_id=order_id,
        amount=amount,
        customer_phone=customer_phone,
        risk_signals=risk_signals or [],
        correlation_id=correlation_id or uuid.uuid4().hex[:12],
        details=alert_details,
    )


def create_shipping_alert(
    title: str,
    severity: AlertSeverity | str,
    *,
    tenant_id: str | None = None,
    order_id: str | None = None,
    awb: str | None = None,
    carrier: str = "bosta",
    correlation_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SlackAlert:
    """Create a shipping-related alert.

    Use for: AWB creation failures, delivery SLA breaches, API outages.
    """
    alert_details = details or {}
    if awb:
        alert_details["awb"] = awb
    alert_details["carrier"] = carrier

    return SlackAlert(
        severity=AlertSeverity(severity) if isinstance(severity, str) else severity,
        title=f"[SHIPPING] {title}",
        service=AlertService.SHIPPING,
        tenant_id=tenant_id,
        order_id=order_id,
        correlation_id=correlation_id or uuid.uuid4().hex[:12],
        details=alert_details,
    )


def create_infrastructure_alert(
    title: str,
    severity: AlertSeverity | str,
    *,
    component: str | None = None,
    metric_value: float | None = None,
    threshold: float | None = None,
    correlation_id: str | None = None,
    details: dict[str, Any] | None = None,
    runbook_url: str | None = None,
) -> SlackAlert:
    """Create an infrastructure alert.

    Use for: DB issues, Redis down, disk space, latency spikes.
    """
    alert_details = details or {}
    if component:
        alert_details["component"] = component
    if metric_value is not None:
        alert_details["metric_value"] = metric_value
    if threshold is not None:
        alert_details["threshold"] = threshold

    return SlackAlert(
        severity=AlertSeverity(severity) if isinstance(severity, str) else severity,
        title=f"[INFRA] {title}",
        service=AlertService.INFRASTRUCTURE,
        correlation_id=correlation_id or uuid.uuid4().hex[:12],
        details=alert_details,
        runbook_url=runbook_url,
    )
