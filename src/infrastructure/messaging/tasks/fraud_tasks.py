"""Fraud detection Celery task.

Runs asynchronous fraud checks after an order is created so the checkout
response is never delayed by the risk-scoring logic.
"""

import asyncio
import logging

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
    name="tasks.fraud_check_order",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def fraud_check_order_task(
    self,
    order_id: str,
    store_id: str,
    tenant_id: str | None,
    order_number: str,
    total_cents: int,
    currency: str,
    payment_method: str | None,
    customer_name: str | None,
    customer_email: str | None,
    shipping_address: dict,
    billing_address: dict | None,
    ip_address: str | None,
) -> dict:
    """Assess fraud risk for a newly created order and persist the result.

    Runs the full FraudDetectionService pipeline (velocity, amount, address
    mismatch) and stores a RiskAssessmentModel row.  High/critical orders
    are logged as warnings so they surface in alerting tools.
    """

    async def _run():
        from uuid import UUID

        from sqlalchemy import text

        from src.application.services.fraud_detection_service import (
            FraudDetectionService,
        )
        from src.infrastructure.database.connection import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            # Replicate get_db_session setup for the public schema + RLS context
            await session.execute(text("SET search_path TO public"))
            if tenant_id:
                await session.execute(
                    text("SELECT set_config('app.current_tenant', :tid, true)"),
                    {"tid": tenant_id},
                )
            else:
                await session.execute(
                    text("SELECT set_config('app.current_tenant', '', true)")
                )

            service = FraudDetectionService()
            result = await service.assess_order(
                order_id=UUID(order_id),
                store_id=UUID(store_id),
                tenant_id=UUID(tenant_id) if tenant_id else None,
                order_number=order_number,
                total_cents=total_cents,
                currency=currency,
                payment_method=payment_method,
                customer_name=customer_name,
                customer_email=customer_email,
                shipping_address=shipping_address,
                billing_address=billing_address,
                ip_address=ip_address,
                session=session,
            )
            await session.commit()

        return {
            "order_id": order_id,
            "risk_score": result.risk_score,
            "risk_level": result.risk_level,
            "requires_review": result.requires_review,
        }

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error(f"Fraud check failed for order {order_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)
