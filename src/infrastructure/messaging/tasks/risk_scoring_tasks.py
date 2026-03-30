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

        from sqlalchemy import select, text, update

        from src.application.use_cases.shopify.phone_hash import normalize_and_hash
        from src.application.use_cases.shopify.risk_scoring_engine import (
            compute_network_score,
            score_order,
        )
        from src.config.settings import get_settings
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.database.models.tenant.network_reputation import (
            NetworkReputationModel,
        )
        from src.infrastructure.database.models.tenant.risk_assessment import (
            RiskAssessmentModel,
        )
        from src.infrastructure.database.models.tenant.shopify_app_settings import (
            ShopifyAppSettingsModel,
        )

        # Look up network reputation for this phone number
        net_score = 55
        net_label = "new_to_network"
        if phone:
            salt = get_settings().platform_secret_salt
            if salt:
                phone_hash = normalize_and_hash(phone, salt)
                if phone_hash:
                    async with AsyncSessionLocal() as lookup_session:
                        await lookup_session.execute(text("SET search_path TO public"))
                        result = await lookup_session.execute(
                            select(NetworkReputationModel).where(
                                NetworkReputationModel.phone_hash == phone_hash
                            )
                        )
                        rep = result.scalar_one_or_none()
                        if rep is not None:
                            net_score, _conf, net_label = compute_network_score(
                                total_orders=rep.total_network_orders,
                                total_rtos=rep.total_network_rtos,
                                total_deliveries=rep.total_successful_deliveries,
                                total_refunds=rep.total_refunds,
                                contributing_store_count=rep.contributing_store_count,
                            )

        full_result = score_order(
            total_cents=total_cents,
            payment_method=payment_method,
            customer_total_orders=customer_total_orders,
            customer_cancellation_rate=customer_cancellation_rate,
            address=address,
            phone=phone,
            avg_order_cents=avg_order_cents,
            network_score=net_score,
            network_label=net_label,
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

            # Auto-cancel safety gate: now that score_type="final", apply
            # the auto-cancel threshold if the score warrants it.
            # Auto-cancel is deliberately deferred from the preliminary
            # score to avoid destroying legitimate orders.
            sid = UUID(store_id)
            settings_row = await session.execute(
                select(ShopifyAppSettingsModel).where(
                    ShopifyAppSettingsModel.store_id == sid
                )
            )
            settings = settings_row.scalar_one_or_none()
            if (
                settings
                and settings.cod_risk_scoring_enabled
                and full_result.risk_score >= settings.auto_cancel_threshold
            ):
                await session.execute(
                    update(RiskAssessmentModel)
                    .where(RiskAssessmentModel.id == UUID(assessment_id))
                    .values(action_taken="auto_cancelled")
                )
                logger.info(
                    "Auto-cancel applied on final score: assessment=%s score=%d threshold=%d",
                    assessment_id,
                    full_result.risk_score,
                    settings.auto_cancel_threshold,
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
