"""Charge the platform commission to the merchant wallet on order paid.

Pay-as-you-go tenants fund NUMU through a per-PAID-order commission debited
from their prepaid wallet (``merchant_wallets``). This handler subscribes to
``OrderPaidEvent`` — the single point every payment path converges on
(gateway webhooks, InstaPay proof approval, COD delivery confirmation) —
and to ``OrderStatusChangedEvent`` for full-refund reversals.

Idempotency: duplicate event delivery is absorbed by the partial unique
index on (order_id) per kind in ``wallet_transactions`` — a second charge
for the same order is a constraint-level no-op. Missed events are healed
by ``wallet_reconciliation_task`` (daily beat), so this handler never
retries; it logs and moves on.

Money: ``event.total`` is a float — NEVER used for arithmetic. The handler
re-reads ``OrderModel.total`` (integer cents) inside its own transaction.
Commission is floored (``//``) in the merchant's favor.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from src.core.entities.wallet import WalletTransactionKind
from src.core.events.order_events import OrderPaidEvent, OrderStatusChangedEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal

logger = get_logger(__name__)

COMMISSION_BPS_DIVISOR = 10_000


async def handle_commission_charge_on_order_paid(event: OrderPaidEvent) -> None:
    """Debit the wallet for a paid order. Never raises."""
    log = logger.bind(
        order_id=str(event.order_id),
        order_number=event.order_number,
        store_id=str(event.store_id),
    )

    from src.application.services.wallet_service import WalletService
    from src.infrastructure.database.models.public.tenant import TenantModel
    from src.infrastructure.database.models.public.wallet import MerchantWalletModel
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.repositories.store_repository import StoreRepository

    async with AsyncSessionLocal() as session:
        try:
            notify_level: int | None = None
            tenant_id = None
            balance_after: int | None = None

            async with session.begin():
                store = await StoreRepository(session).get_by_id(event.store_id)
                if store is None:
                    log.warning("wallet_commission_store_not_found")
                    return
                tenant_id = store.tenant_id

                tenant = (
                    await session.execute(
                        select(TenantModel).where(TenantModel.id == tenant_id)
                    )
                ).scalar_one_or_none()
                if tenant is None:
                    log.warning("wallet_commission_tenant_not_found")
                    return

                wallet = (
                    await session.execute(
                        select(MerchantWalletModel).where(
                            MerchantWalletModel.tenant_id == tenant_id
                        )
                    )
                ).scalar_one_or_none()

                service = WalletService(session)
                bps = service.effective_commission_bps(tenant, wallet)
                if bps <= 0:
                    return  # subscription tenant — no per-order fee

                row = (
                    await session.execute(
                        select(OrderModel.total, OrderModel.currency).where(
                            OrderModel.id == event.order_id
                        )
                    )
                ).one_or_none()
                if row is None:
                    log.warning("wallet_commission_order_not_found")
                    return
                total_cents, order_currency = int(row.total), row.currency

                egp_cents, fx_meta = await _to_egp_cents(
                    session, total_cents, order_currency
                )
                if egp_cents is None:
                    # No FX rate — do NOT silently charge a wrong amount.
                    # The reconciliation sweep re-attempts daily and keeps
                    # flagging until a rate row exists.
                    log.error(
                        "wallet_commission_no_fx_rate",
                        currency=order_currency,
                    )
                    return

                commission_cents = (egp_cents * bps) // COMMISSION_BPS_DIVISOR
                if commission_cents <= 0:
                    return

                meta = {"bps": bps, **fx_meta}
                tx = await service.apply_entry(
                    tenant_id=tenant_id,
                    kind=WalletTransactionKind.COMMISSION,
                    amount_cents=-commission_cents,
                    order_id=event.order_id,
                    note=f"Commission for order {event.order_number}",
                    meta=meta,
                )
                if tx is None:
                    return  # duplicate delivery — already charged

                balance_after = tx.balance_after_cents
                wallet = await service.get_or_create_wallet(tenant_id)
                notify_level = service.bump_warning_level(wallet)
                log.info(
                    "wallet_commission_charged",
                    commission_cents=commission_cents,
                    bps=bps,
                    balance_after_cents=balance_after,
                )

            # Post-commit: cache + (deduped) low-balance notification.
            service = WalletService(session)
            await service.invalidate_cache(tenant_id)
            if notify_level:
                _enqueue_warning_notification(tenant_id, notify_level, balance_after)
        except Exception:
            log.exception("wallet_commission_handler_failed")


async def handle_commission_reversal_on_refund(
    event: OrderStatusChangedEvent,
) -> None:
    """Reverse the order's commission on a FULL refund. Never raises.

    v1 policy: only ``new_status == "refunded"`` (full refund) auto-reverses.
    Partial refunds keep the full commission; support can issue a pro-rata
    ``adjustment`` entry from the admin panel.
    """
    if event.new_status != "refunded":
        return

    log = logger.bind(
        order_id=str(event.order_id),
        order_number=event.order_number,
        store_id=str(event.store_id),
    )

    from src.application.services.wallet_service import WalletService
    from src.infrastructure.database.models.public.wallet import (
        WalletTransactionModel,
    )

    async with AsyncSessionLocal() as session:
        try:
            tenant_id = None
            async with session.begin():
                commission = (
                    await session.execute(
                        select(WalletTransactionModel).where(
                            WalletTransactionModel.order_id == event.order_id,
                            WalletTransactionModel.kind
                            == WalletTransactionKind.COMMISSION.value,
                        )
                    )
                ).scalar_one_or_none()
                if commission is None:
                    return  # never charged (not payg, FX skip, ...)

                tenant_id = commission.tenant_id
                service = WalletService(session)
                tx = await service.apply_entry(
                    tenant_id=tenant_id,
                    kind=WalletTransactionKind.COMMISSION_REVERSAL,
                    amount_cents=abs(commission.amount_cents),
                    order_id=event.order_id,
                    note=f"Commission reversal for refunded order {event.order_number}",
                    meta={"reversed_transaction_id": str(commission.id)},
                )
                if tx is None:
                    return  # already reversed
                # A reversal only raises the balance; sync the ladder down.
                wallet = await service.get_or_create_wallet(tenant_id)
                service.bump_warning_level(wallet)
                log.info(
                    "wallet_commission_reversed",
                    amount_cents=abs(commission.amount_cents),
                    balance_after_cents=tx.balance_after_cents,
                )

            await WalletService(session).invalidate_cache(tenant_id)
        except Exception:
            log.exception("wallet_commission_reversal_failed")


async def _to_egp_cents(
    session, amount_cents: int, currency: str
) -> tuple[int | None, dict]:
    """Convert an order total to EGP cents via ``currency_rates``.

    Returns ``(None, {})`` when no rate exists — caller must skip, not guess.
    """
    if currency == "EGP":
        return amount_cents, {}

    from src.infrastructure.database.models.public.currency_rate import (
        CurrencyRateModel,
    )

    rate_row = (
        await session.execute(
            select(CurrencyRateModel).where(
                CurrencyRateModel.base == currency,
                CurrencyRateModel.target == "EGP",
            )
        )
    ).scalar_one_or_none()
    if rate_row is None:
        return None, {}

    converted = int(Decimal(amount_cents) * rate_row.rate)
    return converted, {
        "original_amount_cents": amount_cents,
        "original_currency": currency,
        "fx_rate": str(rate_row.rate),
    }


def _enqueue_warning_notification(
    tenant_id, level: int, balance_cents: int | None
) -> None:
    """Fire-and-forget Celery enqueue; wallet writes never depend on it."""
    try:
        from src.infrastructure.messaging.tasks.wallet_notification_tasks import (
            send_wallet_warning_task,
        )

        send_wallet_warning_task.delay(
            tenant_id=str(tenant_id),
            level=level,
            balance_cents=balance_cents,
        )
    except Exception:  # noqa: BLE001 — broker down must not fail the charge
        logger.warning("wallet_warning_enqueue_failed", tenant_id=str(tenant_id))
