"""Celery task — purge Shopify personal data older than the retention period.

Satisfies the Shopify Protected Customer Data requirement:
  "Do you have retention periods that make sure personal data isn't kept longer than needed?"

Runs daily via Celery Beat.  Deletes rows from:
  - risk_assessments
  - payment_transactions
  - automation_logs
whose ``created_at`` is older than ``SHOPIFY_DATA_RETENTION_DAYS`` (default 180).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_purge(retention_days: int) -> dict[str, int]:
    """Synchronous wrapper that drives the async purge."""
    return asyncio.run(_async_purge(retention_days))


async def _async_purge(retention_days: int) -> dict[str, int]:
    from src.infrastructure.database.connection import AsyncSessionLocal as async_session_factory
    from src.infrastructure.database.models.tenant.automation_log import (
        AutomationLogModel,
    )
    from src.infrastructure.database.models.tenant.payment_transaction import (
        PaymentTransactionModel,
    )
    from src.infrastructure.database.models.tenant.risk_assessment import (
        RiskAssessmentModel,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    results: dict[str, int] = {}

    async with async_session_factory() as session:
        for label, model in [
            ("risk_assessments", RiskAssessmentModel),
            ("payment_transactions", PaymentTransactionModel),
            ("automation_logs", AutomationLogModel),
        ]:
            stmt = delete(model).where(model.created_at < cutoff)  # type: ignore[attr-defined]
            result = await session.execute(stmt)
            results[label] = result.rowcount  # type: ignore[assignment]

        await session.commit()

    return results


@celery_app.task(
    name="tasks.purge_shopify_pii",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def purge_shopify_pii(self, retention_days: int | None = None) -> dict:
    """Delete Shopify personal-data rows older than *retention_days*.

    Falls back to ``settings.shopify_data_retention_days`` (default 180).
    """
    if retention_days is None:
        from src.config import settings
        retention_days = settings.shopify_data_retention_days

    try:
        logger.info(
            "Starting Shopify PII purge (retention=%d days) …", retention_days
        )
        counts = _run_purge(retention_days)
        logger.info("PII purge complete: %s", counts)
        return {"status": "ok", "deleted": counts}
    except Exception as exc:
        logger.exception("PII purge failed, retrying …")
        raise self.retry(exc=exc)
