"""Celery task: full 5-factor async risk score for Shopify COD orders.

Upgrade path
------------
1. ``orders/create`` webhook calls ``score_order_fast()`` synchronously
   (2 factors, <200ms) and persists ``score_type="preliminary"``.
2. This task is enqueued immediately after persistence and runs within
   the Celery ``default`` queue.
3. When the task completes it overwrites the risk score with the full
   5-factor result and sets ``score_type="final"`` + ``scored_at``.

Safety rule: if this task fails for any reason the preliminary score
remains operative.  The order is NEVER left in a broken state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from uuid import UUID

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """Run an async coroutine in a persistent per-worker event loop."""
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.compute_full_risk_score",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    soft_time_limit=15,
)
def compute_full_risk_score(
    self,
    assessment_id: str,
    store_id: str,
    total_cents: int,
    payment_method: str | None,
    customer_total_orders: int,
    customer_cancellation_rate: float | None,
    address: str | None,
    phone: str | None,
    avg_order_cents: int,
) -> dict:
    """Compute and persist the full 5-factor risk score for a COD order.

    Parameters
    ----------
    assessment_id:
        UUID string of the ``risk_assessments`` row to update.
    store_id:
        UUID string of the owning store (for logging context only).
    total_cents:
        Order total in smallest currency unit.
    payment_method:
        Gateway name from Shopify (e.g. ``"cash_on_delivery"``).
    customer_total_orders:
        Lifetime order count for this customer from the Shopify payload.
    customer_cancellation_rate:
        Historical cancellation rate (0.0–1.0) or ``None`` if unknown.
    address:
        Formatted shipping address string for quality assessment.
    phone:
        Raw phone number string for format validation.
    avg_order_cents:
        Store rolling average at the time of the order (from Redis).
    """

    async def _run() -> dict:
        from datetime import datetime

        from sqlalchemy import text, update

        from src.application.use_cases.shopify.risk_scoring_engine import score_order
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.database.models.tenant.risk_assessment import (
            RiskAssessmentModel,
        )

        full_result = score_order(
            total_cents=total_cents,
            payment_method=payment_method,
            customer_total_orders=customer_total_orders,
            customer_cancellation_rate=customer_cancellation_rate,
            address=address,
            phone=phone,
            avg_order_cents=avg_order_cents,
        )

        factors_json = [
            {
                "name": f.factor,
                "score": f.score,
                "weight": f.weight,
                "detail": f.reason,
            }
            for f in full_result.factors
        ]

        async with AsyncSessionLocal() as session:
            await session.execute(text("SET search_path TO public"))
            await session.execute(
                update(RiskAssessmentModel)
                .where(RiskAssessmentModel.id == UUID(assessment_id))
                .values(
                    risk_score=full_result.risk_score,
                    risk_level=full_result.risk_level,
                    suggested_action=full_result.suggested_action,
                    factors=factors_json,
                    score_type="final",
                    scored_at=datetime.now(UTC),
                )
            )
            await session.commit()

        logger.info(
            "Full risk score computed: assessment=%s store=%s score=%d level=%s",
            assessment_id,
            store_id,
            full_result.risk_score,
            full_result.risk_level,
        )
        return {
            "assessment_id": assessment_id,
            "risk_score": full_result.risk_score,
            "risk_level": full_result.risk_level,
            "score_type": "final",
        }

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error(
            "Full risk scoring failed for assessment %s (store %s): %s",
            assessment_id,
            store_id,
            exc,
            exc_info=True,
        )
        # Retry only on transient infrastructure errors.
        # On all other failures the preliminary score stays operative —
        # do NOT re-raise so the order is never left in a broken state.
        exc_str = str(exc).lower()
        if any(kw in exc_str for kw in ("connection", "timeout", "unavailable")):
            raise self.retry(exc=exc)
        return {
            "assessment_id": assessment_id,
            "error": str(exc),
            "score_type": "preliminary",
        }
