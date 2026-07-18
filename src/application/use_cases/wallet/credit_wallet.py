"""Shared credit path: a top-up intent succeeded → credit the wallet.

Called from three places with different idempotency keys:

* platform Paymob webhook   — ``paymob:{transaction_id}``
* InstaPay proof auto-pass  — ``proof:{proof_id}``
* admin proof approval      — ``proof:{proof_id}`` (same key: a webhook
  retry racing an admin double-click still credits exactly once)

The caller owns the transaction; this function flushes only. Post-commit,
call :func:`notify_topup_credited` (cache invalidation + email enqueue).
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.wallet_service import WalletService
from src.core.entities.wallet import TopupIntentStatus, WalletTransactionKind
from src.infrastructure.database.models.public.wallet import (
    WalletTopupIntentModel,
    WalletTransactionModel,
)

logger = logging.getLogger(__name__)


async def credit_topup_intent(
    session: AsyncSession,
    *,
    intent: WalletTopupIntentModel,
    idempotency_key: str,
    source: str,
    actor_user_id: UUID | None = None,
) -> WalletTransactionModel | None:
    """Mark the intent succeeded and credit its amount to the wallet.

    Idempotent at two layers: the intent-status guard (only a
    non-terminal intent transitions) and the ledger's unique
    ``idempotency_key``. Returns the ledger row, or ``None`` when this
    credit was already applied.
    """
    service = WalletService(session)
    tx = await service.apply_entry(
        tenant_id=intent.tenant_id,
        kind=WalletTransactionKind.TOPUP,
        amount_cents=intent.amount_cents,
        currency=intent.currency,
        topup_intent_id=intent.id,
        idempotency_key=idempotency_key,
        actor_user_id=actor_user_id,
        note=f"Top-up via {intent.method} ({intent.special_reference})",
        meta={"source": source},
    )
    if tx is None:
        logger.info(
            "wallet_topup_already_credited",
            extra={
                "intent_id": str(intent.id),
                "idempotency_key": idempotency_key,
            },
        )
        # Keep the intent status consistent even on replay.
        if intent.status != TopupIntentStatus.SUCCEEDED.value:
            intent.status = TopupIntentStatus.SUCCEEDED.value
        return None

    intent.status = TopupIntentStatus.SUCCEEDED.value
    intent.credited_transaction_id = tx.id

    # A credit only raises the balance — sync the warning ladder down.
    wallet = await service.get_or_create_wallet(intent.tenant_id)
    service.bump_warning_level(wallet)

    await session.flush()
    logger.info(
        "wallet_topup_credited",
        extra={
            "intent_id": str(intent.id),
            "tenant_id": str(intent.tenant_id),
            "amount_cents": intent.amount_cents,
            "balance_after_cents": tx.balance_after_cents,
            "source": source,
        },
    )
    return tx


async def notify_topup_credited(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    amount_cents: int,
    balance_after_cents: int,
) -> None:
    """Post-commit side effects: cache invalidation + credited email."""
    await WalletService(session).invalidate_cache(tenant_id)
    try:
        from src.infrastructure.messaging.tasks.wallet_notification_tasks import (
            send_wallet_topup_credited_task,
        )

        send_wallet_topup_credited_task.delay(
            tenant_id=str(tenant_id),
            amount_cents=amount_cents,
            balance_cents=balance_after_cents,
        )
    except Exception:  # noqa: BLE001 — broker down must not fail the credit
        logger.warning(
            "wallet_topup_notify_enqueue_failed",
            extra={"tenant_id": str(tenant_id)},
        )
