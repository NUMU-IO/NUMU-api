"""Slack Block Kit message formatters.

Converts SlackAlert objects into Slack Block Kit JSON payloads.
Supports:
- Standard alert format with header, fields, context
- Fraud alerts with risk signals
- Payment alerts with amount formatting
- Action buttons (View in Sentry, Runbook, etc.)
"""

from typing import Any

from src.config import settings
from src.infrastructure.slack.alerts import AlertService, AlertSeverity, SlackAlert


def format_amount(amount: Any, currency: str = "EGP") -> str:
    """Format monetary amount for display."""
    if amount is None:
        return "N/A"
    try:
        value = float(amount)
        if currency == "EGP":
            return f"EGP {value:,.2f}"
        return f"{currency} {value:,.2f}"
    except (TypeError, ValueError):
        return str(amount)


def format_timestamp(alert: SlackAlert) -> str:
    """Format timestamp with timezone for Egypt."""
    # Format: 2024-01-15 14:32:05 EET
    return alert.timestamp.strftime("%Y-%m-%d %H:%M:%S") + " UTC"


def build_header_block(alert: SlackAlert) -> dict[str, Any]:
    """Build header block with severity and service."""
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{alert.severity_emoji} {alert.severity.value} | {alert.service.value.upper()}",
            "emoji": True,
        },
    }


def build_title_block(alert: SlackAlert) -> dict[str, Any]:
    """Build title/description section."""
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{alert.title}*",
        },
    }


def build_environment_badge() -> str:
    """Get environment badge text."""
    env = settings.environment
    if env == "production":
        return "`PROD`"
    elif env == "staging":
        return "`STAGING`"
    return "`DEV`"


def build_fields_block(alert: SlackAlert) -> dict[str, Any] | None:
    """Build fields section with alert context."""
    fields = []

    # Environment
    fields.append({
        "type": "mrkdwn",
        "text": f"*Environment:*\n{build_environment_badge()}",
    })

    # Tenant
    if alert.tenant_id:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Tenant:*\n`{alert.tenant_id}`",
        })

    # Order ID
    if alert.order_id:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Order:*\n`{alert.order_id}`",
        })

    # Amount
    if alert.amount is not None:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Amount:*\n{format_amount(alert.amount, alert.currency)}",
        })

    # Fraud score (from details)
    if "fraud_score" in alert.details:
        score = alert.details["fraud_score"]
        score_emoji = (
            "\U0001f534"
            if float(score) > 0.9
            else "\U0001f7e0"
            if float(score) > 0.7
            else "\U0001f7e1"
        )
        fields.append({
            "type": "mrkdwn",
            "text": f"*Fraud Score:*\n`{score}` {score_emoji}",
        })

    # Gateway (for payment alerts)
    if "gateway" in alert.details:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Gateway:*\n{alert.details['gateway']}",
        })

    # Component (for infra alerts)
    if "component" in alert.details:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Component:*\n{alert.details['component']}",
        })

    # AWB (for shipping alerts)
    if "awb" in alert.details:
        fields.append({
            "type": "mrkdwn",
            "text": f"*AWB:*\n`{alert.details['awb']}`",
        })

    # Carrier
    if "carrier" in alert.details:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Carrier:*\n{alert.details['carrier'].title()}",
        })

    if not fields:
        return None

    return {
        "type": "section",
        "fields": fields[:8],  # Slack limit: 10 fields, keep some room
    }


def build_risk_signals_block(alert: SlackAlert) -> dict[str, Any] | None:
    """Build risk signals section for fraud alerts."""
    if not alert.risk_signals:
        return None

    signals_text = "\n".join(f"\u2022 {signal}" for signal in alert.risk_signals[:6])

    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Risk Signals:*\n{signals_text}",
        },
    }


def build_customer_details_block(alert: SlackAlert) -> dict[str, Any] | None:
    """Build customer details for fraud alerts (internal only)."""
    if alert.service != AlertService.FRAUD:
        return None

    fields = []

    if alert.customer_phone:
        # Partially mask phone for security
        masked = (
            alert.customer_phone[:7] + "xxx" + alert.customer_phone[-4:]
            if len(alert.customer_phone) > 11
            else alert.customer_phone
        )
        fields.append({
            "type": "mrkdwn",
            "text": f"*Phone:*\n`{masked}`",
        })

    # Add other details from alert.details
    if "ip_location" in alert.details:
        fields.append({
            "type": "mrkdwn",
            "text": f"*IP Location:*\n{alert.details['ip_location']}",
        })

    if "device" in alert.details:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Device:*\n{alert.details['device']}",
        })

    if "session_duration" in alert.details:
        fields.append({
            "type": "mrkdwn",
            "text": f"*Session:*\n{alert.details['session_duration']}s",
        })

    if not fields:
        return None

    return {
        "type": "section",
        "fields": fields[:4],
    }


def build_context_block(alert: SlackAlert) -> dict[str, Any]:
    """Build context footer with timestamp and correlation ID."""
    context_text = (
        f"\U0001f550 {format_timestamp(alert)} | `corr:{alert.correlation_id}`"
    )

    return {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": context_text},
        ],
    }


def build_mentions_block(alert: SlackAlert) -> dict[str, Any] | None:
    """Build mentions section."""
    mentions = []

    # Add explicit mention users
    for user_id in alert.mention_users:
        mentions.append(f"<@{user_id}>")

    # Add on-call for CRITICAL alerts
    if alert.severity == AlertSeverity.CRITICAL and settings.slack_user_oncall:
        if settings.slack_user_oncall not in alert.mention_users:
            mentions.append(f"<@{settings.slack_user_oncall}>")

    # Add fraud lead for fraud alerts
    if alert.service == AlertService.FRAUD and settings.slack_user_fraud_lead:
        if settings.slack_user_fraud_lead not in alert.mention_users:
            mentions.append(f"<@{settings.slack_user_fraud_lead}>")

    # Add notes with potential @mentions
    if alert.notes:
        mentions.append(alert.notes)

    if not mentions:
        return None

    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "cc: " + " ".join(mentions),
        },
    }


def build_actions_block(alert: SlackAlert) -> dict[str, Any] | None:
    """Build action buttons."""
    elements = []

    # Add explicit actions
    for action in alert.actions:
        button: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": action.text},
        }
        if action.url:
            button["url"] = action.url
        if action.action_id:
            button["action_id"] = action.action_id
        if action.style:
            button["style"] = action.style
        if action.value:
            button["value"] = action.value
        elements.append(button)

    # Add Sentry link if available
    if alert.sentry_url:
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "View in Sentry"},
            "url": alert.sentry_url,
        })

    # Add Runbook link if available
    if alert.runbook_url:
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Runbook"},
            "url": alert.runbook_url,
        })

    # Add Dashboard link if available
    if alert.dashboard_url:
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Dashboard"},
            "url": alert.dashboard_url,
        })

    if not elements:
        return None

    return {
        "type": "actions",
        "elements": elements[:5],  # Slack limit: 5 elements
    }


def build_divider() -> dict[str, Any]:
    """Build divider block."""
    return {"type": "divider"}


def format_alert_to_blocks(alert: SlackAlert) -> dict[str, Any]:
    """Convert SlackAlert to Slack Block Kit payload.

    Returns a complete payload ready for webhook posting.
    """
    blocks: list[dict[str, Any]] = []

    # Header with severity and service
    blocks.append(build_header_block(alert))

    # Title/description
    blocks.append(build_title_block(alert))

    # Main fields (environment, tenant, order, amount, etc.)
    fields_block = build_fields_block(alert)
    if fields_block:
        blocks.append(fields_block)

    # Divider before details (for fraud alerts)
    if alert.risk_signals or (
        alert.service == AlertService.FRAUD and alert.customer_phone
    ):
        blocks.append(build_divider())

    # Risk signals (fraud alerts)
    risk_block = build_risk_signals_block(alert)
    if risk_block:
        blocks.append(risk_block)

    # Customer details (fraud alerts, internal only)
    customer_block = build_customer_details_block(alert)
    if customer_block:
        blocks.append(customer_block)

    # Context (timestamp, correlation ID)
    blocks.append(build_context_block(alert))

    # Mentions
    mentions_block = build_mentions_block(alert)
    if mentions_block:
        blocks.append(mentions_block)

    # Action buttons
    actions_block = build_actions_block(alert)
    if actions_block:
        blocks.append(actions_block)

    # Build payload
    payload: dict[str, Any] = {
        "blocks": blocks,
    }

    # Add fallback text for notifications
    payload["text"] = f"{alert.severity_emoji} [{alert.severity.value}] {alert.title}"

    return payload


def format_simple_message(
    text: str, severity: AlertSeverity = AlertSeverity.INFO
) -> dict[str, Any]:
    """Format a simple text message (no blocks)."""
    emoji = {
        AlertSeverity.CRITICAL: "\U0001f534",
        AlertSeverity.WARN: "\U0001f7e1",
        AlertSeverity.INFO: "\U0001f535",
    }[severity]

    return {
        "text": f"{emoji} {text}",
    }
