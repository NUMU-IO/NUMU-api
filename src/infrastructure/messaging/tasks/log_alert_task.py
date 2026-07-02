"""Celery task: deliver ``log.alert(...)`` events to the configured webhook.

Provider-agnostic — POSTs the alert payload as JSON to
``settings.log_alert_webhook_url`` (Slack / n8n / any HTTP incoming webhook).
No-op when unconfigured, so ``log.alert(...)`` degrades to log-only.

Enqueued (by name) from :func:`src.core.logging._dispatch_alert` so the logging
module never imports Celery at import time.
"""

import httpx

from src.config.settings import settings
from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="tasks.dispatch_log_alert",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def dispatch_log_alert(self, payload: dict):
    """POST a ``log.alert`` payload to the configured webhook.

    Args:
        payload: the alert event dict ({"event", "level", ...extra fields}).
    """
    url = settings.log_alert_webhook_url
    if not url:
        return {"delivered": False, "reason": "no_webhook_configured"}
    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return {"delivered": True, "status": resp.status_code}
    except Exception as e:  # noqa: BLE001 — retry any delivery failure
        logger.warning("log_alert_delivery_failed", error=str(e))
        raise self.retry(exc=e)
