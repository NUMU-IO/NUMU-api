"""Periodic maintenance tasks for the Trust Network.

1. ``retry_stuck_preliminary_scores`` — re-enqueue scoring for orders
   stuck in ``score_type='preliminary'`` for over 5 minutes.
2. ``cleanup_expired_payment_links`` — delete expired, uncompleted
   payment link sessions older than 48 hours.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.retry_stuck_preliminary_scores",
    soft_time_limit=60,
)
def retry_stuck_preliminary_scores(max_age_minutes: int = 5, batch_size: int = 50):
    """Find orders stuck in 'preliminary' state and re-enqueue full scoring.

    An order is considered stuck if its ``score_type`` is still
    ``'preliminary'`` and it was created more than ``max_age_minutes`` ago.
    This catches cases where the Celery broker was down when the webhook
    handler tried to enqueue ``compute_full_risk_score``.
    """

    async def _run():
        from sqlalchemy import select, text

        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.database.models.tenant.risk_assessment import (
            RiskAssessmentModel,
        )

        cutoff = datetime.now(UTC) - timedelta(minutes=max_age_minutes)
        retried = 0

        async with AsyncSessionLocal() as session:
            await session.execute(text("SET search_path TO public"))
            result = await session.execute(
                select(RiskAssessmentModel)
                .where(
                    RiskAssessmentModel.score_type == "preliminary",
                    RiskAssessmentModel.created_at <= cutoff,
                )
                .order_by(RiskAssessmentModel.created_at.asc())
                .limit(batch_size)
            )
            stuck_orders = list(result.scalars().all())

        if not stuck_orders:
            return {"retried": 0}

        from src.infrastructure.messaging.tasks.risk_scoring_tasks import (
            compute_full_risk_score,
        )

        for order in stuck_orders:
            try:
                compute_full_risk_score.delay(
                    assessment_id=str(order.id),
                    store_id=str(order.store_id),
                    total_cents=order.total_cents or 0,
                    payment_method=order.payment_method,
                    customer_total_orders=0,
                    customer_cancellation_rate=None,
                    address=None,
                    phone=None,
                    avg_order_cents=80_000,
                )
                retried += 1
            except Exception as exc:
                logger.error(
                    "Failed to re-enqueue stuck assessment %s: %s", order.id, exc
                )

        logger.info("Retried %d stuck preliminary scores (cutoff=%s)", retried, cutoff)
        return {"retried": retried}

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("retry_stuck_preliminary_scores failed: %s", exc, exc_info=True)
        return {"error": str(exc)}


@celery_app.task(
    name="tasks.cleanup_expired_payment_links",
    soft_time_limit=60,
)
def cleanup_expired_payment_links(expired_hours: int = 48):
    """Delete expired, uncompleted payment link sessions.

    Only deletes sessions where:
    - ``status`` is ``'pending'``
    - ``expires_at`` is more than ``expired_hours`` ago
    """

    async def _run():
        from sqlalchemy import delete, text

        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.database.models.tenant.payment_link_session import (
            PaymentLinkSessionModel,
        )

        cutoff = datetime.now(UTC) - timedelta(hours=expired_hours)

        async with AsyncSessionLocal() as session:
            await session.execute(text("SET search_path TO public"))
            result = await session.execute(
                delete(PaymentLinkSessionModel).where(
                    PaymentLinkSessionModel.status == "pending",
                    PaymentLinkSessionModel.expires_at <= cutoff,
                )
            )
            deleted = result.rowcount or 0
            await session.commit()

        if deleted:
            logger.info("Cleaned up %d expired payment link sessions", deleted)
        return {"deleted": deleted}

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error("cleanup_expired_payment_links failed: %s", exc, exc_info=True)
        return {"error": str(exc)}
