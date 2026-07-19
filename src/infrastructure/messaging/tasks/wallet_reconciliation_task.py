"""Celery beat — heal missed wallet commissions + ledger integrity check.

The commission handler on ``OrderPaidEvent`` is fire-and-forget: a worker
crash or broker hiccup can drop a charge. This nightly sweep makes the
wallet eventually consistent:

1. **Missed commissions** — for every commission-bearing tenant, find
   orders paid in the last ``LOOKBACK_DAYS`` with no matching
   ``wallet_transactions`` row of kind ``commission`` and charge them
   (``idempotency_key=recon:{order_id}``; the partial unique index on
   (order_id, kind) also protects against racing a late live handler).
   Orders whose commission was already reversed are naturally skipped
   because the commission row still exists.
2. **Integrity** — recompute each wallet's balance from the ledger; on
   drift, log loudly and self-heal ``balance_cents`` to the ledger sum
   (the ledger is authoritative by design).

Skips (no FX rate, suspended wallet) are logged per order and re-attempted
on the next run — nothing is silently dropped.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 7


@celery_app.task(name="tasks.wallet_commission_reconciliation")
def wallet_commission_reconciliation_task() -> dict:
    """Entry point for the daily beat schedule."""
    return asyncio.run(_async_run())


async def _async_run() -> dict:  # noqa: PLR0915 — linear sweep
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from src.application.services.wallet_service import (
        WalletService,
        WalletSuspendedError,
    )
    from src.core.entities.order import PaymentStatus
    from src.core.entities.wallet import WalletTransactionKind
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.tenant import TenantModel
    from src.infrastructure.database.models.public.wallet import (
        MerchantWalletModel,
        WalletTransactionModel,
    )
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.events.handlers.wallet_commission_handler import (
        COMMISSION_BPS_DIVISOR,
        _to_egp_cents,
    )

    charged = 0
    skipped = 0
    healed = 0
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)

    async with AsyncSessionLocal() as session:
        # ── Pass 1: missed commissions ────────────────────────────────
        # Commission-bearing tenants: payg plan OR an explicit override.
        tenants = (
            (
                await session.execute(
                    select(TenantModel)
                    .outerjoin(
                        MerchantWalletModel,
                        MerchantWalletModel.tenant_id == TenantModel.id,
                    )
                    .where(
                        (TenantModel.plan == "payg")
                        | (MerchantWalletModel.commission_bps_override.isnot(None))
                    )
                )
            )
            .scalars()
            .unique()
            .all()
        )

        for tenant in tenants:
            wallet = (
                await session.execute(
                    select(MerchantWalletModel).where(
                        MerchantWalletModel.tenant_id == tenant.id
                    )
                )
            ).scalar_one_or_none()
            service = WalletService(session)
            bps = await service.effective_commission_bps_admin(tenant, wallet)
            if bps <= 0:
                continue

            commission_exists = (
                select(WalletTransactionModel.id)
                .where(
                    WalletTransactionModel.order_id == OrderModel.id,
                    WalletTransactionModel.kind
                    == WalletTransactionKind.COMMISSION.value,
                )
                .exists()
            )
            missed = (
                await session.execute(
                    select(
                        OrderModel.id,
                        OrderModel.order_number,
                        OrderModel.total,
                        OrderModel.currency,
                    )
                    .join(StoreModel, StoreModel.id == OrderModel.store_id)
                    .where(
                        StoreModel.tenant_id == tenant.id,
                        OrderModel.payment_status == PaymentStatus.PAID,
                        OrderModel.paid_at.isnot(None),
                        OrderModel.paid_at >= since,
                        ~commission_exists,
                    )
                )
            ).all()

            for order in missed:
                try:
                    async with session.begin_nested():
                        egp_cents, fx_meta = await _to_egp_cents(
                            session, int(order.total), order.currency
                        )
                        if egp_cents is None:
                            skipped += 1
                            logger.error(
                                "wallet_recon_no_fx_rate",
                                extra={
                                    "tenant_id": str(tenant.id),
                                    "order_id": str(order.id),
                                    "currency": order.currency,
                                },
                            )
                            continue
                        commission_cents = (egp_cents * bps) // COMMISSION_BPS_DIVISOR
                        if commission_cents <= 0:
                            continue
                        tx = await service.apply_entry(
                            tenant_id=tenant.id,
                            kind=WalletTransactionKind.COMMISSION,
                            amount_cents=-commission_cents,
                            order_id=order.id,
                            idempotency_key=f"recon:{order.id}",
                            note=(
                                "Commission (reconciliation) for order "
                                f"{order.order_number}"
                            ),
                            meta={"bps": bps, "source": "recon", **fx_meta},
                        )
                        if tx is not None:
                            charged += 1
                            logger.warning(
                                "wallet_recon_missed_commission_charged",
                                extra={
                                    "tenant_id": str(tenant.id),
                                    "order_id": str(order.id),
                                    "commission_cents": commission_cents,
                                },
                            )
                except WalletSuspendedError:
                    skipped += 1
                    logger.warning(
                        "wallet_recon_suspended_skip",
                        extra={"tenant_id": str(tenant.id)},
                    )
                    break  # rest of this tenant's orders will fail too
                except Exception:
                    skipped += 1
                    logger.exception(
                        "wallet_recon_order_failed",
                        extra={
                            "tenant_id": str(tenant.id),
                            "order_id": str(order.id),
                        },
                    )

            await session.commit()
            wallet = (
                await session.execute(
                    select(MerchantWalletModel).where(
                        MerchantWalletModel.tenant_id == tenant.id
                    )
                )
            ).scalar_one_or_none()
            if wallet is not None:
                await service.invalidate_cache(tenant.id)

        # ── Pass 2: ledger integrity ──────────────────────────────────
        drift_rows = (
            await session.execute(
                select(
                    MerchantWalletModel.id,
                    MerchantWalletModel.tenant_id,
                    MerchantWalletModel.balance_cents,
                    func.coalesce(
                        func.sum(WalletTransactionModel.amount_cents), 0
                    ).label("ledger_sum"),
                )
                .outerjoin(
                    WalletTransactionModel,
                    WalletTransactionModel.wallet_id == MerchantWalletModel.id,
                )
                .group_by(MerchantWalletModel.id)
                .having(
                    MerchantWalletModel.balance_cents
                    != func.coalesce(func.sum(WalletTransactionModel.amount_cents), 0)
                )
            )
        ).all()

        for row in drift_rows:
            logger.error(
                "wallet_balance_drift_healed",
                extra={
                    "wallet_id": str(row.id),
                    "tenant_id": str(row.tenant_id),
                    "stored_balance_cents": row.balance_cents,
                    "ledger_sum_cents": int(row.ledger_sum),
                },
            )
            wallet = (
                await session.execute(
                    select(MerchantWalletModel)
                    .where(MerchantWalletModel.id == row.id)
                    .with_for_update()
                )
            ).scalar_one()
            wallet.balance_cents = int(row.ledger_sum)
            healed += 1
        await session.commit()
        for row in drift_rows:
            await WalletService(session).invalidate_cache(row.tenant_id)

    result = {"charged": charged, "skipped": skipped, "drift_healed": healed}
    logger.info("wallet_reconciliation_done", extra=result)
    return result
