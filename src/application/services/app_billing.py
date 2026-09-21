"""Paid apps: price, subscription, entitlement, and the partner's 80/20 share.

How money moves (apps plan, Phase 7):

- **The merchant pays from their NUMU wallet.** Each paid period is one
  ``wallet_transactions`` row of kind ``app_charge``. The wallet is funded by
  the rails that already exist: InstaPay and Vodafone Cash receipts (live)
  and Kashier cards (built into wallet top-ups, but live only once NUMU's
  platform Kashier account is configured). So an app charge never talks to a
  payment gateway, and Kashier arrives as a wallet top-up rail, not here.
  ``ChargeSource`` is the seam if a direct card charge is ever wanted.
- **A Partner App sale credits the partner 80%** (``partner_ledger_entries``,
  kind ``sale``). NUMU keeps 20% (OD-4). A NUMU App keeps 100% and writes no
  ledger row.
- **Payouts are manual bank transfers** an admin records as a ``payout``
  entry. The balance NUMU owes a partner is the sum of their entries.

Access follows the money: a paid app's token and webhooks work only while the
store's subscription covers ``now`` (``is_entitled``). Free and external-priced
apps are always entitled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.wallet_service import (
    WalletService,
    WalletSuspendedError,
)
from src.core.entities.app import AppStatus
from src.core.entities.wallet import WalletTransactionKind
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.app_billing import (
    AppSubscriptionModel,
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)

logger = get_logger(__name__)

#: OD-4: the partner keeps 80% of what the merchant pays, NUMU 20%.
PARTNER_SHARE_BPS = 8_000
CYCLE_DAYS = {"monthly": 30, "annual": 365}
#: Partner Agreement § 11.5 (draft): a lapsed payment keeps the app working
#: for 3 days before it is switched off on that store.
GRACE = timedelta(days=3)
#: Partner Agreement § 11.4 (draft, [LEGAL REVIEW]): a sale becomes payable to
#: the partner 30 days after NUMU collects it.
HOLD = timedelta(days=30)


class InsufficientFundsError(Exception):
    """The merchant's wallet can't cover the charge."""

    def __init__(self, needed_cents: int, balance_cents: int):
        super().__init__(f"needs {needed_cents}, wallet has {balance_cents}")
        self.needed_cents = needed_cents
        self.balance_cents = balance_cents


class NotPaidError(ValueError):
    """The app has no recurring price, so there is nothing to subscribe to."""


@dataclass(frozen=True)
class AppPrice:
    price_cents: int
    currency: str
    cycle: str


def app_price(app: AppModel) -> AppPrice | None:
    """The app's recurring price, or None when it is free or priced outside NUMU."""
    pricing = (app.manifest or {}).get("pricing") or {}
    if pricing.get("plan") != "recurring":
        return None
    return AppPrice(
        price_cents=int(pricing["price_cents"]),
        currency=pricing.get("currency") or "EGP",
        cycle=pricing["cycle"],
    )


def split(gross_cents: int) -> tuple[int, int]:
    """``(partner_share, platform_fee)`` of a sale, in whole piasters.

    The fee rounds half up and the partner gets the rest, so the two always
    add up to exactly what the merchant paid.
    """
    fee = (gross_cents * (10_000 - PARTNER_SHARE_BPS) + 5_000) // 10_000
    return gross_cents - fee, fee


class ChargeSource(Protocol):
    """Where a paid period's money comes from."""

    async def charge(
        self,
        *,
        tenant_id: UUID,
        amount_cents: int,
        currency: str,
        key: str,
        note: str,
    ) -> bool:
        """True once the money is taken; False if ``key`` was already charged.

        Raises InsufficientFundsError when it can't be taken.
        """


class WalletChargeSource:
    """Charge the merchant's NUMU wallet (``app_charge`` entries).

    A suspended wallet can't pay (``WalletSuspendedError``). After the
    caller commits, ``invalidate()`` refreshes the cached balances it moved.
    """

    def __init__(self, db: AsyncSession):
        self._wallet = WalletService(db)
        self._touched: set[UUID] = set()

    async def charge(self, *, tenant_id, amount_cents, currency, key, note) -> bool:
        wallet = await self._wallet.get_or_create_wallet(tenant_id, for_update=True)
        if wallet.balance_cents < amount_cents:
            raise InsufficientFundsError(amount_cents, wallet.balance_cents)
        tx = await self._wallet.apply_entry(
            tenant_id=tenant_id,
            kind=WalletTransactionKind.APP_CHARGE,
            amount_cents=-amount_cents,
            currency=currency,
            idempotency_key=key,
            note=note,
        )
        if tx is not None:
            self._touched.add(tenant_id)
        return tx is not None

    async def invalidate(self) -> None:
        for tenant_id in self._touched:
            await self._wallet.invalidate_cache(tenant_id)
        self._touched.clear()


async def subscription_for(
    db: AsyncSession, installation_id: UUID, *, for_update: bool = False
) -> AppSubscriptionModel | None:
    stmt = select(AppSubscriptionModel).where(
        AppSubscriptionModel.installation_id == installation_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


def paid_through(sub: AppSubscriptionModel | None, now: datetime) -> bool:
    """Whether ``now`` falls inside a period actually paid for."""
    return (
        sub is not None
        and sub.status == "active"
        and _aware(sub.current_period_end) > now
    )


def covers(sub: AppSubscriptionModel | None, now: datetime) -> bool:
    """Whether the store may use the app at ``now``: a paid period, plus the
    3-day grace after a lapse. A merchant who cancelled gets no grace."""
    if sub is None or sub.status not in ("active", "past_due"):
        return False
    end = _aware(sub.current_period_end)
    if not (sub.status == "active" and sub.cancel_at_period_end):
        end += GRACE
    return end > now


async def is_entitled(
    db: AsyncSession,
    installation: AppInstallationModel,
    app: AppModel,
    *,
    now: datetime | None = None,
) -> bool:
    """May this installation use the app right now? Free apps: always."""
    if app_price(app) is None:
        return True
    return covers(await subscription_for(db, installation.id), now or _now())


async def subscribe(
    db: AsyncSession,
    *,
    installation: AppInstallationModel,
    app: AppModel,
    source: ChargeSource,
    now: datetime | None = None,
) -> tuple[AppSubscriptionModel, bool]:
    """Pay for one period starting now. Returns ``(subscription, charged)``.

    A no-op when the store is already covered (a double click, or paying
    twice): ``charged`` is then False and nothing is taken. Re-subscribing
    after a lapse or a cancellation takes the app's CURRENT price. Caller
    owns the commit.
    """
    now = now or _now()
    price = app_price(app)
    if price is None:
        raise NotPaidError(app.slug)
    # Serialize per installation: the second of two concurrent clicks sees
    # the first one's period and stops here.
    await db.execute(
        select(AppInstallationModel.id)
        .where(AppInstallationModel.id == installation.id)
        .with_for_update()
    )
    sub = await subscription_for(db, installation.id, for_update=True)
    if paid_through(sub, now):
        if sub.cancel_at_period_end:
            sub.cancel_at_period_end = False  # "resume", free until the period ends
        return sub, False

    end = now + timedelta(days=CYCLE_DAYS[price.cycle])
    key = f"app-sub:{installation.id}:start:{now.isoformat()}"
    charged = await source.charge(
        tenant_id=installation.tenant_id,
        amount_cents=price.price_cents,
        currency=price.currency,
        key=key,
        note=f"{app.slug} ({price.cycle})",
    )
    if not charged:
        return sub, False
    if sub is None:
        sub = AppSubscriptionModel(
            tenant_id=installation.tenant_id,
            store_id=installation.store_id,
            app_id=app.id,
            installation_id=installation.id,
        )
        db.add(sub)
    sub.status = "active"
    sub.price_cents = price.price_cents
    sub.currency = price.currency
    sub.cycle = price.cycle
    sub.current_period_start = now
    sub.current_period_end = end
    sub.cancel_at_period_end = False
    await db.flush()
    await _credit_partner(db, app, sub, price.price_cents, price.currency, key, now)
    logger.info(
        "app_subscription_started",
        app=app.slug,
        store_id=str(installation.store_id),
        price_cents=price.price_cents,
        period_end=end.isoformat(),
    )
    return sub, True


async def cancel(
    db: AsyncSession, installation_id: UUID
) -> AppSubscriptionModel | None:
    """Stop renewing. Access runs to the end of the period already paid for."""
    sub = await subscription_for(db, installation_id, for_update=True)
    if sub is not None and sub.status == "active":
        sub.cancel_at_period_end = True
        await db.flush()
    return sub


async def renew_due(
    db: AsyncSession, *, source: ChargeSource, now: datetime | None = None
) -> dict[str, int]:
    """Charge the next period of every subscription whose period has ended.

    Renewals use the subscriber's price snapshot. A cancelled subscription,
    an uninstalled or disabled app, or a suspended app is not charged; a
    wallet that can't pay moves the subscription to ``past_due`` and access
    stops (the merchant tops up and subscribes again). Idempotent: the charge
    key is the period being bought.
    """
    now = now or _now()
    stats = {"renewed": 0, "cancelled": 0, "past_due": 0}
    rows = (
        (
            await db.execute(
                select(AppSubscriptionModel)
                .where(
                    AppSubscriptionModel.status == "active",
                    AppSubscriptionModel.current_period_end <= now,
                )
                .order_by(AppSubscriptionModel.current_period_end)
                .limit(500)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for sub in rows:
        install = await db.get(AppInstallationModel, sub.installation_id)
        app = await db.get(AppModel, sub.app_id)
        live = (
            install is not None
            and install.is_enabled
            and install.status == "active"
            and app is not None
            and app.status == AppStatus.PUBLISHED
        )
        if sub.cancel_at_period_end or not live:
            sub.status = "cancelled"
            stats["cancelled"] += 1
            continue
        start = _aware(sub.current_period_end)
        key = f"app-sub:{sub.id}:renew:{start.isoformat()}"
        try:
            charged = await source.charge(
                tenant_id=sub.tenant_id,
                amount_cents=sub.price_cents,
                currency=sub.currency,
                key=key,
                note=f"{app.slug} ({sub.cycle}) renewal",
            )
        except (InsufficientFundsError, WalletSuspendedError):
            sub.status = "past_due"
            stats["past_due"] += 1
            logger.info("app_subscription_past_due", app=app.slug, sub=str(sub.id))
            continue
        sub.current_period_start = start
        sub.current_period_end = start + timedelta(days=CYCLE_DAYS[sub.cycle])
        if charged:
            await _credit_partner(db, app, sub, sub.price_cents, sub.currency, key, now)
            stats["renewed"] += 1
    await db.flush()
    return stats


async def _credit_partner(
    db: AsyncSession,
    app: AppModel,
    sub: AppSubscriptionModel,
    gross_cents: int,
    currency: str,
    key: str,
    collected_at: datetime,
) -> PartnerLedgerEntryModel | None:
    """The partner's 80% of one charge. NUMU Apps: nothing to credit."""
    if app.developer_id is None:
        return None
    partner_id = await db.scalar(
        select(PartnerAccountModel.id).where(
            PartnerAccountModel.user_id == app.developer_id
        )
    )
    if partner_id is None:
        # Should not happen: publishing needs an approved partner. NUMU holds
        # the money until an admin records an adjustment.
        logger.error("partner_ledger_no_partner_account", app=app.slug, key=key)
        return None
    share, fee = split(gross_cents)
    entry = PartnerLedgerEntryModel(
        partner_id=partner_id,
        kind="sale",
        amount_cents=share,
        gross_cents=gross_cents,
        platform_fee_cents=fee,
        currency=currency,
        app_id=app.id,
        subscription_id=sub.id,
        idempotency_key=key,
        # The 30-day hold counts from when NUMU collected the money.
        created_at=collected_at,
    )
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except IntegrityError:
        return None  # this charge was already credited
    return entry


async def partner_balance(db: AsyncSession, partner_id: UUID) -> int:
    """What NUMU owes this partner right now, in piasters."""
    return int(
        await db.scalar(
            select(
                func.coalesce(func.sum(PartnerLedgerEntryModel.amount_cents), 0)
            ).where(PartnerLedgerEntryModel.partner_id == partner_id)
        )
        or 0
    )


async def partner_payable(
    db: AsyncSession, partner_id: UUID, *, now: datetime | None = None
) -> int:
    """What may be paid out now: the balance, minus sales still inside the
    30-day hold. Never more than the balance."""
    cutoff = (now or _now()) - HOLD
    held = int(
        await db.scalar(
            select(
                func.coalesce(func.sum(PartnerLedgerEntryModel.amount_cents), 0)
            ).where(
                PartnerLedgerEntryModel.partner_id == partner_id,
                PartnerLedgerEntryModel.kind == "sale",
                PartnerLedgerEntryModel.created_at > cutoff,
            )
        )
        or 0
    )
    return max(0, await partner_balance(db, partner_id) - held)


async def record_adjustment(
    db: AsyncSession,
    *,
    partner_id: UUID,
    amount_cents: int,
    reference: str,
    note: str,
    actor_user_id: UUID,
    currency: str = "EGP",
) -> PartnerLedgerEntryModel:
    """A signed correction, e.g. reversing the partner's share of a refunded
    charge (Partner Agreement § 11.3). One entry per reference."""
    reference, note = reference.strip(), note.strip()
    if amount_cents == 0 or not reference or not note:
        raise ValueError(
            "An adjustment needs a non-zero amount, a reference and a note."
        )
    entry = PartnerLedgerEntryModel(
        partner_id=partner_id,
        kind="adjustment",
        amount_cents=amount_cents,
        currency=currency,
        idempotency_key=f"adjustment:{partner_id}:{reference}",
        reference=reference,
        actor_user_id=actor_user_id,
        note=note,
    )
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except IntegrityError as exc:
        raise ValueError(f"Adjustment {reference} is already recorded.") from exc
    return entry


async def record_payout(
    db: AsyncSession,
    *,
    partner_id: UUID,
    amount_cents: int,
    reference: str,
    actor_user_id: UUID,
    note: str | None = None,
    currency: str = "EGP",
    now: datetime | None = None,
) -> PartnerLedgerEntryModel:
    """Record a bank transfer already sent to the partner (money moves outside
    NUMU). Refuses more than is payable (the balance minus sales still in the
    30-day hold); one entry per transfer reference."""
    reference = reference.strip()
    if amount_cents <= 0 or not reference:
        raise ValueError("A payout needs a positive amount and the transfer reference.")
    # One payout at a time per partner, so two admins can't both pay the balance.
    await db.execute(
        select(PartnerAccountModel.id)
        .where(PartnerAccountModel.id == partner_id)
        .with_for_update()
    )
    payable = await partner_payable(db, partner_id, now=now)
    if amount_cents > payable:
        raise ValueError(
            f"{payable} is payable now (sales stay on hold for 30 days); "
            "a payout can't exceed it."
        )
    entry = PartnerLedgerEntryModel(
        partner_id=partner_id,
        kind="payout",
        amount_cents=-amount_cents,
        currency=currency,
        idempotency_key=f"payout:{partner_id}:{reference}",
        reference=reference,
        actor_user_id=actor_user_id,
        note=note,
    )
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except IntegrityError as exc:
        raise ValueError(f"Transfer {reference} is already recorded.") from exc
    return entry


def subscription_out(sub: AppSubscriptionModel | None, app: AppModel) -> dict[str, Any]:
    """The merchant-facing view of a store's subscription to an app."""
    price = app_price(app)
    now = _now()
    return {
        "paid": price is not None,
        "price_cents": price.price_cents if price else None,
        "currency": price.currency if price else None,
        "cycle": price.cycle if price else None,
        "status": sub.status if sub else None,
        "entitled": price is None or covers(sub, now),
        "current_period_end": sub.current_period_end if sub else None,
        "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
        # What this store pays at renewal (its snapshot), if it differs.
        "subscribed_price_cents": sub.price_cents if sub else None,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """SQLite (tests) hands back naive datetimes; Postgres timestamptz aware."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
