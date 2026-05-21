"""Slack Alert Service - main entry point for sending alerts.

Provides high-level API for sending alerts with:
- Automatic rate limiting and deduplication
- Channel routing based on alert type
- Environment isolation (prod vs non-prod)
- Async queue processing option
"""

import sentry_sdk

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.slack.alerts import (
    AlertService,
    AlertSeverity,
    SlackAlert,
    create_critical_alert,
    create_fraud_alert,
    create_infrastructure_alert,
    create_payment_alert,
    create_shipping_alert,
)
from src.infrastructure.slack.channels import (
    get_all_channels_for_alert,
    get_channel_for_alert,
)
from src.infrastructure.slack.client import SlackClient, get_slack_client
from src.infrastructure.slack.formatters import format_alert_to_blocks
from src.infrastructure.slack.rate_limiter import (
    AlertQueue,
    AlertRateLimiter,
    get_alert_queue,
    get_rate_limiter,
)

logger = get_logger(__name__)


class SlackAlertService:
    """High-level service for sending Slack alerts.

    Usage:
        service = SlackAlertService()

        # Send immediately (with rate limiting)
        await service.send_alert(alert)

        # Queue for background processing
        await service.queue_alert(alert)

        # Convenience methods
        await service.alert_payment_failure(...)
        await service.alert_fraud_detected(...)
    """

    def __init__(
        self,
        client: SlackClient | None = None,
        rate_limiter: AlertRateLimiter | None = None,
        queue: AlertQueue | None = None,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter
        self._queue = queue

    @property
    def client(self) -> SlackClient:
        if self._client is None:
            self._client = get_slack_client()
        return self._client

    @property
    def rate_limiter(self) -> AlertRateLimiter:
        if self._rate_limiter is None:
            self._rate_limiter = get_rate_limiter()
        return self._rate_limiter

    @property
    def queue(self) -> AlertQueue:
        if self._queue is None:
            self._queue = get_alert_queue()
        return self._queue

    async def send_alert(
        self,
        alert: SlackAlert,
        *,
        bypass_rate_limit: bool = False,
        include_escalation: bool = True,
    ) -> bool:
        """Send alert to Slack with rate limiting.

        Args:
            alert: Alert to send
            bypass_rate_limit: Skip rate limiting (use sparingly)
            include_escalation: Also post to #critical if applicable

        Returns:
            True if alert was sent (or suppressed by rate limiting)
        """
        if not settings.slack_enabled:
            logger.debug(
                "slack_alert_skipped",
                reason="slack_disabled",
                correlation_id=alert.correlation_id,
            )
            return False

        # Check rate limiting
        suppressed_count = 0
        if not bypass_rate_limit:
            should_send, suppressed_count = await self.rate_limiter.should_send(alert)
            if not should_send:
                logger.info(
                    "slack_alert_rate_limited",
                    correlation_id=alert.correlation_id,
                    suppressed_count=suppressed_count,
                    severity=alert.severity.value,
                )
                return True  # Suppressed counts as "handled"

        # Add suppressed count to title if any
        if suppressed_count > 0:
            alert = alert.with_suppressed_count(suppressed_count)

        # Get channels to post to
        channels = (
            get_all_channels_for_alert(alert)
            if include_escalation
            else [get_channel_for_alert(alert)]
        )

        # Format message
        payload = format_alert_to_blocks(alert)

        # Send to all channels
        success = True
        for channel in channels:
            try:
                result = await self.client.post_webhook(channel, payload)
                if not result:
                    success = False
                    logger.warning(
                        "slack_alert_send_failed",
                        channel=channel.value,
                        correlation_id=alert.correlation_id,
                    )
            except Exception as e:
                success = False
                logger.error(
                    "slack_alert_exception",
                    channel=channel.value,
                    correlation_id=alert.correlation_id,
                    error=str(e),
                )
                # Capture in Sentry but don't crash
                sentry_sdk.capture_exception(e)

        if success:
            logger.info(
                "slack_alert_sent",
                correlation_id=alert.correlation_id,
                severity=alert.severity.value,
                service=alert.service.value,
                channels=[c.value for c in channels],
            )

        return success

    async def queue_alert(self, alert: SlackAlert) -> bool:
        """Queue alert for background processing.

        Use this for non-critical alerts to avoid blocking request processing.
        """
        return await self.queue.enqueue(alert)

    async def process_queue(self, max_alerts: int = 10) -> int:
        """Process queued alerts (called by Celery task).

        Args:
            max_alerts: Maximum alerts to process in one batch

        Returns:
            Number of alerts processed
        """
        processed = 0

        for _ in range(max_alerts):
            alert = await self.queue.dequeue()
            if alert is None:
                break

            await self.send_alert(alert)
            processed += 1

        if processed > 0:
            logger.info(
                "slack_queue_processed",
                processed=processed,
            )

        return processed

    # =========================================================================
    # Convenience Methods for Common Alert Types
    # =========================================================================

    async def alert_payment_failure(
        self,
        title: str,
        *,
        gateway: str,
        tenant_id: str | None = None,
        order_id: str | None = None,
        amount: float | None = None,
        correlation_id: str | None = None,
        critical: bool = False,
    ) -> bool:
        """Send payment failure alert."""
        from decimal import Decimal

        alert = create_payment_alert(
            title=title,
            severity=AlertSeverity.CRITICAL if critical else AlertSeverity.WARN,
            tenant_id=tenant_id,
            order_id=order_id,
            amount=Decimal(str(amount)) if amount else None,
            gateway=gateway,
            correlation_id=correlation_id,
        )
        return await self.send_alert(alert)

    async def alert_fraud_detected(
        self,
        *,
        order_id: str,
        tenant_id: str,
        fraud_score: float,
        amount: float,
        risk_signals: list[str],
        customer_phone: str | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Send fraud detection alert."""
        from decimal import Decimal

        # Determine severity based on score
        if fraud_score >= 0.95:
            severity = AlertSeverity.CRITICAL
            title = "High-risk COD order auto-blocked"
        elif fraud_score >= 0.80:
            severity = AlertSeverity.WARN
            title = "High-risk COD order requires review"
        else:
            severity = AlertSeverity.INFO
            title = "Suspicious COD order flagged"

        alert = create_fraud_alert(
            title=title,
            severity=severity,
            tenant_id=tenant_id,
            order_id=order_id,
            amount=Decimal(str(amount)),
            fraud_score=fraud_score,
            customer_phone=customer_phone,
            risk_signals=risk_signals,
            correlation_id=correlation_id,
        )
        return await self.send_alert(alert)

    async def alert_shipping_failure(
        self,
        title: str,
        *,
        carrier: str = "bosta",
        tenant_id: str | None = None,
        order_id: str | None = None,
        awb: str | None = None,
        correlation_id: str | None = None,
        critical: bool = False,
    ) -> bool:
        """Send shipping failure alert."""
        alert = create_shipping_alert(
            title=title,
            severity=AlertSeverity.CRITICAL if critical else AlertSeverity.WARN,
            tenant_id=tenant_id,
            order_id=order_id,
            awb=awb,
            carrier=carrier,
            correlation_id=correlation_id,
        )
        return await self.send_alert(alert)

    async def alert_infrastructure(
        self,
        title: str,
        *,
        component: str,
        critical: bool = False,
        metric_value: float | None = None,
        threshold: float | None = None,
        runbook_url: str | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Send infrastructure alert."""
        alert = create_infrastructure_alert(
            title=title,
            severity=AlertSeverity.CRITICAL if critical else AlertSeverity.WARN,
            component=component,
            metric_value=metric_value,
            threshold=threshold,
            runbook_url=runbook_url,
            correlation_id=correlation_id,
        )
        return await self.send_alert(alert)

    async def alert_security(
        self,
        title: str,
        *,
        details: dict | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Send security alert (always CRITICAL)."""
        alert = create_critical_alert(
            title=f"[SECURITY] {title}",
            service=AlertService.SECURITY,
            correlation_id=correlation_id,
            details=details,
            mention_users=[settings.slack_user_oncall]
            if settings.slack_user_oncall
            else [],
        )
        return await self.send_alert(alert)


# Global service instance
_slack_alert_service: SlackAlertService | None = None


def get_slack_alert_service() -> SlackAlertService:
    """Get or create global Slack alert service."""
    global _slack_alert_service
    if _slack_alert_service is None:
        _slack_alert_service = SlackAlertService()
    return _slack_alert_service


# Convenience export
slack_alert_service = get_slack_alert_service()
