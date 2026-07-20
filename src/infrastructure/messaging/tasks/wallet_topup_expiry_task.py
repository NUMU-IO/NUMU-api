"""Celery beat — expire stale wallet top-up intents.

Runs every 15 minutes (pattern: ``instapay_expiry_task``). Transitions:

* ``awaiting_proof`` intents past ``expires_at`` → ``expired`` (the
  merchant never transferred / never uploaded; they can just create a
  fresh top-up).
* ``pending`` (Paymob) intents past ``expires_at`` (24h) → ``expired``
  — the hosted checkout was abandoned. A late webhook for an expired
  intent still credits (guarded transition in the webhook checks for
  both pending and expired) so money is never lost to a slow gateway.

``under_review`` intents are NEVER expired — a human owes the merchant
a decision on the uploaded receipt.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.expire_wallet_topups")
def expire_wallet_topups_task() -> dict:
    return asyncio.run(_async_run())


async def _async_run() -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import update

    from src.core.entities.wallet import TopupIntentStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.wallet import (
        WalletTopupIntentModel,
    )

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(WalletTopupIntentModel)
            .where(
                WalletTopupIntentModel.status.in_((
                    TopupIntentStatus.PENDING.value,
                    TopupIntentStatus.AWAITING_PROOF.value,
                )),
                WalletTopupIntentModel.expires_at.isnot(None),
                WalletTopupIntentModel.expires_at < now,
            )
            .values(
                status=TopupIntentStatus.EXPIRED.value,
                failure_reason="expired",
            )
        )
        await session.commit()
        expired = result.rowcount or 0

    if expired:
        logger.info("wallet_topups_expired", extra={"count": expired})
    return {"expired": expired}
