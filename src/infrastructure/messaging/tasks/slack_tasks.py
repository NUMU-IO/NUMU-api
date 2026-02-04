"""Celery tasks for Slack alert processing.

Provides background processing for:
- Alert queue processing
- Batched/digest alerts for INFO severity
"""

import asyncio

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)


def run_async(coro):
    """Run async code in Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="tasks.process_slack_alert_queue",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_slack_alert_queue(self, max_alerts: int = 10):
    """Process queued Slack alerts.

    Called periodically by Celery Beat to send queued alerts
    while respecting rate limits.

    Args:
        max_alerts: Maximum alerts to process per run
    """
    from src.infrastructure.slack import slack_alert_service

    try:
        processed = run_async(slack_alert_service.process_queue(max_alerts))
        logger.info(
            "slack_queue_task_completed",
            processed=processed,
        )
        return {"processed": processed}
    except Exception as e:
        logger.error(
            "slack_queue_task_failed",
            error=str(e),
        )
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_slack_alert",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def send_slack_alert_task(
    self,
    severity: str,
    title: str,
    service: str,
    tenant_id: str | None = None,
    order_id: str | None = None,
    amount: str | None = None,
    details: dict | None = None,
    correlation_id: str | None = None,
):
    """Send a Slack alert asynchronously.

    Use this task when you want to fire-and-forget an alert
    without blocking the request.

    Args:
        severity: "CRITICAL", "WARN", or "INFO"
        title: Alert title
        service: Service category
        tenant_id: Optional tenant identifier
        order_id: Optional order identifier
        amount: Optional amount (as string for serialization)
        details: Optional additional details
        correlation_id: Optional correlation ID
    """
    from decimal import Decimal

    from src.infrastructure.slack import SlackAlert, slack_alert_service
    from src.infrastructure.slack.alerts import AlertService, AlertSeverity

    try:
        alert = SlackAlert(
            severity=AlertSeverity(severity),
            title=title,
            service=AlertService(service),
            tenant_id=tenant_id,
            order_id=order_id,
            amount=Decimal(amount) if amount else None,
            details=details or {},
            correlation_id=correlation_id,
        )

        result = run_async(slack_alert_service.send_alert(alert))

        logger.info(
            "slack_alert_task_completed",
            correlation_id=correlation_id,
            success=result,
        )
        return {"sent": result, "correlation_id": correlation_id}

    except Exception as e:
        logger.error(
            "slack_alert_task_failed",
            correlation_id=correlation_id,
            error=str(e),
        )
        raise self.retry(exc=e)


@celery_app.task(name="tasks.send_fraud_alert")
def send_fraud_alert_task(
    order_id: str,
    tenant_id: str,
    fraud_score: float,
    amount: float,
    risk_signals: list[str],
    customer_phone: str | None = None,
    correlation_id: str | None = None,
):
    """Send COD fraud alert asynchronously.

    Convenience task for fraud detection service.
    """
    from src.infrastructure.slack import slack_alert_service

    try:
        result = run_async(
            slack_alert_service.alert_fraud_detected(
                order_id=order_id,
                tenant_id=tenant_id,
                fraud_score=fraud_score,
                amount=amount,
                risk_signals=risk_signals,
                customer_phone=customer_phone,
                correlation_id=correlation_id,
            )
        )
        return {"sent": result, "order_id": order_id}
    except Exception as e:
        logger.error(
            "fraud_alert_task_failed",
            order_id=order_id,
            error=str(e),
        )
        return {"sent": False, "error": str(e)}


@celery_app.task(name="tasks.send_payment_alert")
def send_payment_alert_task(
    title: str,
    gateway: str,
    tenant_id: str | None = None,
    order_id: str | None = None,
    amount: float | None = None,
    critical: bool = False,
    correlation_id: str | None = None,
):
    """Send payment alert asynchronously.

    Convenience task for payment services.
    """
    from src.infrastructure.slack import slack_alert_service

    try:
        result = run_async(
            slack_alert_service.alert_payment_failure(
                title=title,
                gateway=gateway,
                tenant_id=tenant_id,
                order_id=order_id,
                amount=amount,
                critical=critical,
                correlation_id=correlation_id,
            )
        )
        return {"sent": result, "order_id": order_id}
    except Exception as e:
        logger.error(
            "payment_alert_task_failed",
            order_id=order_id,
            error=str(e),
        )
        return {"sent": False, "error": str(e)}
