"""Celery sweep for back-in-stock subscriptions (Phase 3.5).

Runs hourly. For each store that has any pending product_subscriptions
rows, scan the products those subscriptions reference; for any product
that's now in stock, fan out the notification email to subscribers and
stamp `notified_at` so the same row doesn't re-fire on the next sweep.

Why hourly instead of real-time:
    A real-time hook (fire on stock-flip) is tempting but every order
    decrements then potentially re-increments stock during refund
    windows; hooking that path would either email at every flip (spam)
    or require complex coalescing logic. Hourly batch matches Shopify's
    documented behavior and is the right tradeoff for v1.

Email template + delivery is intentionally NOT in this file — it lives
in the existing notification_tasks.py + email-templates infrastructure.
This task just enqueues delivery via the same dispatcher used by
abandoned-cart and shipping-update emails.
"""

import asyncio
import logging
from typing import Any

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro: Any) -> Any:
    """Re-use a single task event loop across invocations.

    Mirrors abandoned_cart_tasks.run_async — Celery prefork workers
    don't reset the event loop between tasks, so creating a new loop
    each call leaks descriptors.
    """
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


# Per-sweep cap so a single store with thousands of stockouts doesn't
# stall the worker for hours. Surplus rolls into the next hour's sweep.
MAX_NOTIFICATIONS_PER_SWEEP = 5_000


@celery_app.task(
    name="tasks.product_subscription_sweep",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
)
def product_subscription_sweep_task(self):
    """Sweep pending back-in-stock subscriptions and notify subscribers."""
    try:
        result = run_async(_sweep())
        logger.info("back-in-stock sweep complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("back-in-stock sweep failed")
        raise self.retry(exc=exc)


async def _sweep() -> dict[str, int]:
    """Core sweep logic.

    Algorithm:
      1. Pull distinct (store_id, product_id) pairs that have at least
         one un-notified subscription row.
      2. For each pair, load the product and check `is_in_stock`.
      3. When in stock, pull the pending subscribers (capped per pair),
         enqueue email per subscriber, batch-stamp `notified_at`.

    Side effects: writes notification rows via the existing email
    pipeline; updates `product_subscriptions.notified_at`.
    """

    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.product_subscription import (
        ProductSubscriptionModel,
    )
    from src.infrastructure.repositories.product_repository import ProductRepository
    from src.infrastructure.repositories.product_subscription_repository import (
        ProductSubscriptionRepository,
    )

    sent = 0
    skipped_oos = 0
    products_checked = 0

    async with AsyncSessionLocal() as session:
        # Fetch distinct pending (store_id, product_id) targets. We
        # don't pull the full subscription rows here — that's the
        # second phase, after the in-stock filter.
        targets = (
            await session.execute(
                select(
                    ProductSubscriptionModel.store_id,
                    ProductSubscriptionModel.product_id,
                )
                .where(ProductSubscriptionModel.notified_at.is_(None))
                .distinct()
                .limit(MAX_NOTIFICATIONS_PER_SWEEP)
            )
        ).all()

        product_repo = ProductRepository(session)
        sub_repo = ProductSubscriptionRepository(session)

        for store_id, product_id in targets:
            products_checked += 1
            product = await product_repo.get_by_id(product_id)
            if not product:
                # Product was deleted; pending subs against it can't be
                # delivered. Mark them notified anyway so we stop
                # rescanning. (Empty mark_notified is a no-op.)
                pending = await sub_repo.list_pending_for_product(
                    product_id, limit=1000
                )
                if pending:
                    await sub_repo.mark_notified([s.id for s in pending])
                continue

            if not product.is_in_stock:
                skipped_oos += 1
                continue

            pending = await sub_repo.list_pending_for_product(product_id, limit=1000)
            if not pending:
                continue

            # Fan out emails. We use enqueue rather than synchronous
            # send so a flaky SMTP doesn't take down the sweep — failed
            # individual deliveries are retried by the email task's
            # own backoff. Importing the email task here keeps the
            # module-import cycle simple.
            from src.infrastructure.messaging.tasks.notification_tasks import (
                send_back_in_stock_email_task,
            )

            for sub in pending:
                send_back_in_stock_email_task.delay(
                    store_id=str(store_id),
                    product_id=str(product_id),
                    email=sub.email,
                    variant_id=str(sub.variant_id) if sub.variant_id else None,
                )
                sent += 1

            # Batch-stamp notified_at for the just-enqueued subs. If
            # the email itself fails downstream we accept that — Phase
            # 5 wires DLQ + retry visibility for transient SMTP issues;
            # for v1, idempotence beats deliverability guarantees.
            await sub_repo.mark_notified([s.id for s in pending])

    return {
        "products_checked": products_checked,
        "skipped_out_of_stock": skipped_oos,
        "notifications_sent": sent,
    }
