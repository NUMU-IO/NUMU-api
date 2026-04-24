"""Celery beat task: sweep stale InstaPay intents + cancel their orders.

Runs every 60 seconds. Four passes per run:

  * ``AWAITING_PAYMENT`` past ``expires_at`` — customer never uploaded
    a proof. Transition the intent to ``EXPIRED`` and cancel + restock
    the order.
  * ``PROOF_RECEIVED`` whose ``expires_at + grace_hours`` has passed —
    customer uploaded in time but the merchant never reviewed. Escalate
    by cancelling the order so it doesn't sit PENDING indefinitely.
  * **R2 retention** — proofs older than ``image_retention_days`` on
    terminal orders (cancelled/refunded/delivered) get their R2 object
    deleted and ``proof_image_key`` cleared. DB row stays as audit.
  * **Idempotency-key TTL** — proof rows older than
    ``idempotency_key_retention_days`` have ``idempotency_key`` nulled
    so the scoped uniqueness constraint doesn't accumulate forever.

The first two passes narrow to each order's tenant for the write. The
last two operate under pure RLS bypass because they touch every tenant
in the bucket / table uniformly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None

DEFAULT_PROOF_REVIEW_GRACE_HOURS = 48
DEFAULT_IMAGE_RETENTION_DAYS = 90
DEFAULT_IDEMPOTENCY_KEY_RETENTION_DAYS = 30


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.expire_instapay_orders",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def expire_instapay_orders_task(
    self,
    proof_review_grace_hours: int = DEFAULT_PROOF_REVIEW_GRACE_HOURS,
    image_retention_days: int = DEFAULT_IMAGE_RETENTION_DAYS,
    idempotency_key_retention_days: int = DEFAULT_IDEMPOTENCY_KEY_RETENTION_DAYS,
):
    """Scan + expire + cancel + retain. Returns a stats dict for beat logs."""
    try:
        stats = _run_async(
            _sweep(
                proof_review_grace_hours=proof_review_grace_hours,
                image_retention_days=image_retention_days,
                idempotency_key_retention_days=idempotency_key_retention_days,
            )
        )
        if any(
            stats.get(k)
            for k in ("expired", "escalated", "images_purged", "idempotency_cleared")
        ):
            logger.info("instapay_expiry_sweep", **stats)
        return stats
    except Exception as exc:
        logger.exception("instapay_expiry_sweep_failed")
        raise self.retry(exc=exc)


async def _sweep(
    *,
    proof_review_grace_hours: int,
    image_retention_days: int,
    idempotency_key_retention_days: int,
) -> dict:
    from src.core.entities.instapay import InstapayIntentStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.instapay_intent_repository import (
        InstapayIntentRepository,
    )
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.payment_proof_repository import (
        PaymentProofRepository,
    )
    from src.infrastructure.tenancy.rls import (
        enable_rls_bypass,
        narrow_to_tenant,
    )

    stats = {
        "scanned": 0,
        "expired": 0,
        "escalated": 0,
        "cancelled": 0,
        "images_purged": 0,
        "idempotency_cleared": 0,
        "errors": 0,
    }

    async with AsyncSessionLocal() as session:
        # ── Pass 1: pre-fetch expiry lists under bypass ─────────────
        await enable_rls_bypass(session)
        intent_repo = InstapayIntentRepository(session)

        awaiting_expired = await intent_repo.list_expired_awaiting_payment()
        stuck_review = await intent_repo.list_stuck_proof_received(
            grace_hours=proof_review_grace_hours,
        )
        stats["scanned"] = len(awaiting_expired) + len(stuck_review)

        # ── Pass 2: iterate, narrowing per tenant for each write ────
        for intent, next_status, bucket in [
            (i, InstapayIntentStatus.EXPIRED, "expired") for i in awaiting_expired
        ] + [(i, InstapayIntentStatus.EXPIRED, "escalated") for i in stuck_review]:
            try:
                await narrow_to_tenant(session, intent.tenant_id)
                await intent_repo.update_status(intent.id, next_status)
                stats[bucket] += 1

                order_repo = OrderRepository(session)
                order = await order_repo.get_by_id(intent.order_id)
                if order is None:
                    logger.warning(
                        "instapay_expiry_order_missing",
                        intent_id=str(intent.id),
                    )
                elif order.can_be_cancelled:
                    reason = (
                        "InstaPay proof not reviewed within grace window"
                        if bucket == "escalated"
                        else "InstaPay payment window expired"
                    )
                    order.cancel(reason=reason)
                    await order_repo.update(order)
                    stats["cancelled"] += 1
            except Exception:
                stats["errors"] += 1
                logger.exception(
                    "instapay_expiry_intent_failed",
                    intent_id=str(intent.id),
                )
            finally:
                try:
                    await enable_rls_bypass(session)
                except Exception:
                    logger.exception("instapay_expiry_bypass_reset_failed")

        # Metrics: fire per-bucket counters so the dashboard can alert
        # on a sudden spike in escalations (merchants going offline)
        # vs. routine expiries (customers abandoning the payment flow).
        from src.infrastructure.external_services.instapay.metrics import (
            sweeper_runs_total,
        )

        if stats["expired"]:
            sweeper_runs_total.inc(stats["expired"], bucket="expired")
        if stats["escalated"]:
            sweeper_runs_total.inc(stats["escalated"], bucket="escalated")

        # ── Pass 3: R2 retention (terminal orders, cross-tenant) ────
        if image_retention_days > 0:
            stats["images_purged"] = await _purge_old_images(
                session,
                older_than=datetime.now(UTC) - timedelta(days=image_retention_days),
            )

        # ── Pass 4: idempotency-key TTL (cross-tenant) ──────────────
        if idempotency_key_retention_days > 0:
            proof_repo = PaymentProofRepository(session)
            stats["idempotency_cleared"] = await proof_repo.clear_old_idempotency_keys(
                older_than=datetime.now(UTC)
                - timedelta(days=idempotency_key_retention_days),
            )

        await session.commit()

    return stats


async def _purge_old_images(session, *, older_than: datetime) -> int:
    """Delete R2 objects for retained proof images and null their keys.

    Storage service is resolved lazily so test environments that stub
    it don't need Redis / S3 credentials just to import this module.
    Each delete/clear is isolated — one failing R2 delete doesn't stop
    the batch.
    """
    from src.api.dependencies.services import get_storage_service
    from src.infrastructure.repositories.payment_proof_repository import (
        PaymentProofRepository,
    )

    proof_repo = PaymentProofRepository(session)
    candidates = await proof_repo.list_purgeable_images(older_than=older_than)
    if not candidates:
        return 0

    try:
        storage = get_storage_service()
    except Exception:
        logger.exception("instapay_retention_storage_unavailable")
        return 0

    purged = 0
    for proof in candidates:
        try:
            await storage.delete_file(proof.proof_image_key)
            await proof_repo.clear_image_key(proof.id)
            purged += 1
        except Exception:
            logger.exception(
                "instapay_retention_delete_failed",
                proof_id=str(proof.id),
                key=proof.proof_image_key,
            )
    return purged
