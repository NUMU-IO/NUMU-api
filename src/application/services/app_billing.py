"""Paid apps: price, subscription, entitlement, and the partner's 80/20 share.

How money moves (apps plan, Phase 7):

- **The merchant pays from their NUMU wallet.** Each paid period is one
  ``wallet_transactions`` row of kind ``app_charge``. The wallet is funded by
  the rails that already exist: InstaPay and Vodafone Cash receipts (live)
  and Kashier cards (built into wallet top-ups, but live only once NUMU's
  platform Kashier account is configured). So an app charge never talks to a
  payment gateway, and Kashier arrives as a wallet top-up rail, not here.
  ``ChargeSource`` is the seam if a direct card charge is ever wanted.
- **A Partner App sale credits the partner their share**
  (``partner_ledger_entries``, kind ``sale``): ``partner_accounts.share_bps``,
  80% by default (OD-4). NUMU keeps the rest as its fee. A NUMU App keeps
  100% and writes no ledger row.
- **VAT (14%) is on NUMU's fee only, added on top**: the merchant pays the
  app's price plus VAT on NUMU's fee, and NUMU issues a numbered invoice for
  its fee and that VAT (``app_fee_invoices``). The partner's share is
  unaffected and the partner handles their own tax.
- **Partner coupons are funded by the partner**: NUMU's fee (and its VAT)
  is computed on the full list price and the discount comes out of the
  partner's share, capped at it.
- **Payouts are manual bank transfers** an admin records as a ``payout``
  entry. The balance NUMU owes a partner is the sum of their entries.

Access follows the money: a paid app's token and webhooks work only while the
store's subscription covers ``now`` (``is_entitled``). Free and external-priced
apps are always entitled.

A recurring app may offer a free trial: a store's first subscription to it
starts a trial period with no charge (once per store and app, ``app_trials``),
and renewal at its end charges as usual. Usage charges (``record_usage``) are
taken from the wallet as the app reports them, up to the cap the merchant
approved for the period, and credit the partner through the same 80/20 path.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, select, text
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
    AppCouponModel,
    AppCouponRedemptionModel,
    AppFeeInvoiceModel,
    AppSubscriptionModel,
    AppTrialModel,
    AppUsageRecordModel,
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.wallet import WalletTransactionModel
from src.infrastructure.tenancy.repository import TenantRepository

logger = get_logger(__name__)

#: OD-4: by default the partner keeps 80% of the list price, NUMU 20%.
PARTNER_SHARE_BPS = 8_000
VAT_BPS = 1_400
CYCLE_DAYS = {"monthly": 30, "annual": 365}
#: Partner Agreement § 11.5 (draft): a lapsed payment keeps the app working
#: for 3 days before it is switched off on that store.
GRACE = timedelta(days=3)
#: Partner Agreement § 11.4 (draft, [LEGAL REVIEW]): a sale becomes payable to
#: the partner 30 days after NUMU collects it.
HOLD = timedelta(days=30)
TRIAL_WARNING = timedelta(days=3)


class InsufficientFundsError(Exception):
    """The merchant's wallet can't cover the charge."""

    def __init__(self, needed_cents: int, balance_cents: int):
        super().__init__(f"needs {needed_cents}, wallet has {balance_cents}")
        self.needed_cents = needed_cents
        self.balance_cents = balance_cents


class CouponError(ValueError):
    """A coupon the store can't use. ``code`` is the API error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class NotPaidError(ValueError):
    """The app has no recurring price, so there is nothing to subscribe to."""


class UsageError(ValueError):
    """A usage charge NUMU refuses. ``code`` is the API error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AppPrice:
    price_cents: int
    currency: str
    cycle: str
    trial_days: int = 0
    #: ``{"unit", "price_cents"?, "cap_cents"}`` for metered charges.
    usage: dict | None = None


def app_price(app: AppModel) -> AppPrice | None:
    """The app's price, or None when it is free or priced outside NUMU. A
    usage-only app costs nothing a period and bills monthly periods."""
    pricing = (app.manifest or {}).get("pricing") or {}
    if pricing.get("plan") not in ("recurring", "usage"):
        return None
    return AppPrice(
        price_cents=int(pricing.get("price_cents") or 0),
        currency=pricing.get("currency") or "EGP",
        cycle=pricing.get("cycle") or "monthly",
        trial_days=int(pricing.get("trial_days") or 0),
        usage=pricing.get("usage"),
    )


def bps_of(amount_cents: int, bps: int) -> int:
    """``bps`` basis points of a non-negative amount, rounded half up."""
    return (amount_cents * bps + 5_000) // 10_000


def split(gross_cents: int, share_bps: int = PARTNER_SHARE_BPS) -> tuple[int, int]:
    """``(partner_share, platform_fee)`` of a sale, in whole piasters.

    The fee rounds half up and the partner gets the rest, so the two always
    add up to exactly the list price.
    """
    fee = bps_of(gross_cents, 10_000 - share_bps)
    return gross_cents - fee, fee


@dataclass(frozen=True)
class Quote:
    """One charge: NUMU's fee and its VAT on the full list price, and the
    partner's coupon discount taken from the partner's share only."""

    list_cents: int
    share_bps: int
    fee_cents: int
    vat_cents: int
    discount_cents: int
    capped: bool = False
    partner_id: UUID | None = None
    vat_bps: int = VAT_BPS

    @property
    def partner_cents(self) -> int:
        return self.list_cents - self.fee_cents - self.discount_cents

    @property
    def total_cents(self) -> int:
        return self.list_cents - self.discount_cents + self.vat_cents

    def out(self) -> dict[str, Any]:
        return {
            "list_price_cents": self.list_cents,
            "discount_cents": self.discount_cents,
            "discount_capped": self.capped,
            "vat_cents": self.vat_cents,
            "vat_bps": self.vat_bps,
            "total_cents": self.total_cents,
        }


def quote(
    list_cents: int,
    share_bps: int,
    discount_cents: int = 0,
    partner_id: UUID | None = None,
    vat_bps: int = VAT_BPS,
) -> Quote:
    share, fee = split(list_cents, share_bps)
    return Quote(
        list_cents=list_cents,
        share_bps=share_bps,
        fee_cents=fee,
        vat_cents=bps_of(fee, vat_bps),
        discount_cents=min(discount_cents, share),
        capped=discount_cents > share,
        partner_id=partner_id,
        vat_bps=vat_bps,
    )


def coupon_discount(coupon: AppCouponModel, list_cents: int) -> int:
    if coupon.percent_off:
        return bps_of(list_cents, coupon.percent_off * 100)
    return min(coupon.amount_off_cents or 0, list_cents)


def effective_share_bps(account: PartnerAccountModel | None) -> int:
    if account is None or account.share_bps is None:
        return PARTNER_SHARE_BPS
    return account.share_bps


async def partner_terms(db: AsyncSession, app: AppModel) -> tuple[UUID | None, int]:
    """``(partner_id, share_bps)`` for a charge now. A NUMU App: NUMU keeps
    it all (share 0). A Partner App whose partner account is missing: NUMU
    holds the partner's share (logged at credit time)."""
    if app.developer_id is None:
        return None, 0
    account = await db.scalar(
        select(PartnerAccountModel).where(
            PartnerAccountModel.user_id == app.developer_id
        )
    )
    return (account.id if account else None), effective_share_bps(account)


def sub_vat_bps(sub: AppSubscriptionModel | None) -> int:
    """No VAT for a subscription grandfathered from before VAT on app fees,
    until it ends or is subscribed again."""
    return 0 if sub is not None and sub.vat_grandfathered else VAT_BPS


async def quote_for(
    db: AsyncSession,
    app: AppModel,
    list_cents: int,
    coupon: AppCouponModel | None,
    vat_bps: int = VAT_BPS,
) -> Quote:
    partner_id, share_bps = await partner_terms(db, app)
    discount = coupon_discount(coupon, list_cents) if coupon else 0
    return quote(list_cents, share_bps, discount, partner_id, vat_bps)


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
        meta: dict | None = None,
    ) -> WalletTransactionModel | None:
        """The charge once the money is taken; None if ``key`` was already
        charged.

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

    async def charge(
        self, *, tenant_id, amount_cents, currency, key, note, meta=None
    ) -> WalletTransactionModel | None:
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
            meta=meta,
        )
        if tx is not None:
            self._touched.add(tenant_id)
        return tx

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


def coverage_end(sub: AppSubscriptionModel | None) -> datetime | None:
    """When the store stops being covered: the paid period's end plus the
    3-day grace, or the bare period end once the merchant cancelled. None
    when nothing is live. The entitlement resolver reads this too, so the
    two can never disagree about the grace."""
    if sub is None or sub.status not in ("active", "past_due"):
        return None
    end = _aware(sub.current_period_end)
    if not (sub.status == "active" and sub.cancel_at_period_end):
        end += GRACE
    return end


def covers(sub: AppSubscriptionModel | None, now: datetime) -> bool:
    """Whether the store may use the app at ``now``: a paid period, plus the
    3-day grace after a lapse. A merchant who cancelled gets no grace."""
    end = coverage_end(sub)
    return end is not None and end > now


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
    coupon_code: str | None = None,
) -> tuple[AppSubscriptionModel, bool]:
    """Start a period now. Returns ``(subscription, started)``.

    The store's first subscription to an app with a trial is a free trial
    period; otherwise one period is charged. A no-op when the store is
    already covered (a double click, or paying twice): ``started`` is then
    False and nothing is taken. Re-subscribing after a lapse or a
    cancellation takes the app's CURRENT price. A coupon is redeemed once per
    store and discounts the charged periods it covers. Raises CouponError.
    Caller owns the commit.
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
            await TenantRepository(db).bump_entitlements_version(sub.tenant_id)
        return sub, False

    coupon = (
        await redeemable_coupon(db, app, installation.store_id, coupon_code, now)
        if coupon_code
        else None
    )
    applied = coupon or await _active_coupon(db, sub)
    end = now + timedelta(days=CYCLE_DAYS[price.cycle])
    key = f"app-sub:{installation.id}:start:{now.isoformat()}"
    trial = price.trial_days > 0 and await _claim_trial(db, installation, app)
    tx = q = None
    if trial:
        end = now + timedelta(days=price.trial_days)
    elif price.price_cents:
        q, tx = await _charge(
            db,
            source,
            app,
            tenant_id=installation.tenant_id,
            list_cents=price.price_cents,
            currency=price.currency,
            key=key,
            note=f"{app.slug} ({price.cycle})",
            coupon=applied,
        )
        if tx is None:
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
    sub.is_trial = trial
    sub.vat_grandfathered = False
    sub.usage_cap_cents = price.usage["cap_cents"] if price.usage else None
    sub.usage_unit_cents = price.usage.get("price_cents") if price.usage else None
    await db.flush()
    await TenantRepository(db).bump_entitlements_version(installation.tenant_id)
    if coupon:
        await _redeem(db, coupon, sub)
    if tx is not None:
        if applied:
            _consume_coupon(sub)
        await _book(db, app, sub, q, tx, key, now)
    logger.info(
        "app_subscription_started",
        app=app.slug,
        store_id=str(installation.store_id),
        price_cents=price.price_cents,
        trial=trial,
        period_end=end.isoformat(),
    )
    return sub, True


async def _claim_trial(
    db: AsyncSession, installation: AppInstallationModel, app: AppModel
) -> bool:
    """True the first time a store starts this app's trial, ever."""
    try:
        async with db.begin_nested():
            db.add(
                AppTrialModel(
                    store_id=installation.store_id,
                    app_id=app.id,
                    tenant_id=installation.tenant_id,
                )
            )
            await db.flush()
    except IntegrityError:
        return False
    return True


COUPON_CODE_RE = re.compile(r"^[A-Z0-9_-]{3,40}$")


def normalize_code(code: str) -> str:
    return code.strip().upper()


async def redeemable_coupon(
    db: AsyncSession,
    app: AppModel,
    store_id: UUID,
    code: str,
    now: datetime,
    *,
    lock: bool = True,
) -> AppCouponModel:
    """The app's coupon ``code`` if this store may use it now, else
    CouponError: ``coupon_invalid`` (unknown, disabled or another store's),
    ``coupon_expired``, ``coupon_used`` (this store already redeemed it) or
    ``coupon_exhausted`` (no redemptions left)."""
    stmt = select(AppCouponModel).where(
        AppCouponModel.app_id == app.id,
        AppCouponModel.code == normalize_code(code),
    )
    if lock:
        stmt = stmt.with_for_update()
    coupon = await db.scalar(stmt)
    if (
        coupon is None
        or not coupon.active
        or (coupon.store_id is not None and coupon.store_id != store_id)
    ):
        raise CouponError("coupon_invalid")
    if coupon.expires_at is not None and _aware(coupon.expires_at) <= now:
        raise CouponError("coupon_expired")
    redeemed = await db.scalar(
        select(AppCouponRedemptionModel.id).where(
            AppCouponRedemptionModel.coupon_id == coupon.id,
            AppCouponRedemptionModel.store_id == store_id,
        )
    )
    if redeemed is not None:
        raise CouponError("coupon_used")
    if coupon.max_redemptions is not None:
        used = await db.scalar(
            select(func.count(AppCouponRedemptionModel.id)).where(
                AppCouponRedemptionModel.coupon_id == coupon.id
            )
        )
        if (used or 0) >= coupon.max_redemptions:
            raise CouponError("coupon_exhausted")
    return coupon


def coupon_out(
    c: AppCouponModel, app_name: str | None, redemptions: int
) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "app_id": str(c.app_id),
        "app_name": app_name,
        "code": c.code,
        "percent_off": c.percent_off,
        "amount_off_cents": c.amount_off_cents,
        "duration_cycles": c.duration_cycles,
        "max_redemptions": c.max_redemptions,
        "expires_at": c.expires_at,
        "store_id": str(c.store_id) if c.store_id else None,
        "active": c.active,
        "redemptions": redemptions,
        "created_at": c.created_at,
    }


def redemption_count():
    return (
        select(func.count(AppCouponRedemptionModel.id))
        .where(AppCouponRedemptionModel.coupon_id == AppCouponModel.id)
        .scalar_subquery()
    )


async def _redeem(
    db: AsyncSession, coupon: AppCouponModel, sub: AppSubscriptionModel
) -> None:
    try:
        async with db.begin_nested():
            db.add(
                AppCouponRedemptionModel(
                    coupon_id=coupon.id,
                    store_id=sub.store_id,
                    tenant_id=sub.tenant_id,
                    subscription_id=sub.id,
                )
            )
            await db.flush()
    except IntegrityError:
        raise CouponError("coupon_used") from None
    sub.coupon_id = coupon.id
    sub.coupon_cycles_left = coupon.duration_cycles


async def _active_coupon(
    db: AsyncSession, sub: AppSubscriptionModel | None
) -> AppCouponModel | None:
    if sub is None or sub.coupon_id is None:
        return None
    return await db.get(AppCouponModel, sub.coupon_id)


def _consume_coupon(sub: AppSubscriptionModel) -> None:
    if sub.coupon_cycles_left is None:
        return
    sub.coupon_cycles_left -= 1
    if sub.coupon_cycles_left <= 0:
        sub.coupon_id = None
        sub.coupon_cycles_left = None


async def _charge(
    db: AsyncSession,
    source: ChargeSource,
    app: AppModel,
    *,
    tenant_id: UUID,
    list_cents: int,
    currency: str,
    key: str,
    note: str,
    coupon: AppCouponModel | None = None,
    vat_bps: int = VAT_BPS,
) -> tuple[Quote, WalletTransactionModel | None]:
    """Take the list price, less the coupon, plus VAT on NUMU's fee."""
    q = await quote_for(db, app, list_cents, coupon, vat_bps)
    tx = await source.charge(
        tenant_id=tenant_id,
        amount_cents=q.total_cents,
        currency=currency,
        key=key,
        note=note,
        meta={**q.out(), "share_bps": q.share_bps, "fee_cents": q.fee_cents},
    )
    return q, tx


async def _book(
    db: AsyncSession,
    app: AppModel,
    sub: AppSubscriptionModel,
    q: Quote,
    tx: WalletTransactionModel,
    key: str,
    now: datetime,
) -> None:
    """After a charge: the partner's share and NUMU's fee invoice."""
    await _credit_partner(db, app, sub, q, tx.currency, key, now)
    await issue_fee_invoice(
        db,
        tx=tx,
        kind="invoice",
        q=q,
        store_id=sub.store_id,
        app_id=app.id,
        description=tx.note or app.name,
        now=now,
    )


async def _next_invoice_number(db: AsyncSession, prefix: str) -> str:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('app-fee-invoice-number'))")
        )
    last = await db.scalar(
        select(func.max(AppFeeInvoiceModel.number)).where(
            AppFeeInvoiceModel.number.like(f"{prefix}%")
        )
    )
    seq = int(last[len(prefix) :]) if last else 0
    return f"{prefix}{seq + 1:06d}"


async def issue_fee_invoice(
    db: AsyncSession,
    *,
    tx: WalletTransactionModel,
    kind: str,
    q: Quote,
    store_id: UUID | None,
    app_id: UUID | None,
    description: str,
    now: datetime,
    original: AppFeeInvoiceModel | None = None,
) -> AppFeeInvoiceModel:
    """NUMU's numbered invoice for its fee and VAT on one wallet charge, or
    (``credit_note``, negative amounts) the one reversing it. One per wallet
    row and kind: issuing again returns the first."""
    existing = await db.scalar(
        select(AppFeeInvoiceModel).where(
            AppFeeInvoiceModel.wallet_transaction_id == tx.id,
            AppFeeInvoiceModel.kind == kind,
        )
    )
    if existing is not None:
        return existing
    sign = -1 if kind == "credit_note" else 1
    prefix = "NUMU-CN-" if kind == "credit_note" else "NUMU-"
    invoice = AppFeeInvoiceModel(
        number=await _next_invoice_number(db, f"{prefix}{now.year}-"),
        kind=kind,
        tenant_id=tx.tenant_id,
        store_id=store_id,
        app_id=app_id,
        wallet_transaction_id=tx.id,
        original_id=original.id if original else None,
        list_price_cents=sign * q.list_cents,
        discount_cents=sign * q.discount_cents,
        fee_cents=sign * q.fee_cents,
        vat_cents=sign * q.vat_cents,
        vat_bps=q.vat_bps,
        share_bps=q.share_bps,
        total_cents=sign * q.total_cents,
        currency=tx.currency,
        description=description[:255],
        created_at=now,
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def trial_available(db: AsyncSession, store_id: UUID, app: AppModel) -> bool:
    price = app_price(app)
    if price is None or not price.trial_days:
        return False
    return await db.get(AppTrialModel, (store_id, app.id)) is None


def notice(
    sub: AppSubscriptionModel,
    app: AppModel,
    kind: str,
    dedupe_key: str,
    *,
    important: bool = False,
    link: str | None = None,
    **data: Any,
) -> dict[str, Any]:
    """``emit_notification`` kwargs for a merchant-facing billing event."""
    return {
        "store_id": sub.store_id,
        "tenant_id": sub.tenant_id,
        "category": "payments",
        "kind": kind,
        "data": {"app_name": app.name, "app_slug": app.slug, **data},
        "link": link or f"/apps/{app.slug}",
        "important": important,
        "dedupe_key": dedupe_key,
        "entity_type": "app_subscription",
        "entity_id": sub.id,
    }


def started_notice(sub: AppSubscriptionModel, app: AppModel) -> dict[str, Any]:
    start = _aware(sub.current_period_start).isoformat()
    return notice(
        sub,
        app,
        "app.trial_started" if sub.is_trial else "app.subscription_started",
        f"app-started:{sub.id}:{start}",
        amount_cents=sub.price_cents,
        period_end=_aware(sub.current_period_end).isoformat(),
    )


async def cancel(
    db: AsyncSession, installation_id: UUID
) -> AppSubscriptionModel | None:
    """Stop renewing. Access runs to the end of the period already paid for."""
    sub = await subscription_for(db, installation_id, for_update=True)
    if sub is not None and sub.status == "active":
        sub.cancel_at_period_end = True
        await db.flush()
        await TenantRepository(db).bump_entitlements_version(sub.tenant_id)
    return sub


async def renew_due(
    db: AsyncSession,
    *,
    source: ChargeSource,
    now: datetime | None = None,
    notices: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Charge the next period of every subscription whose period has ended.

    Renewals use the subscriber's price snapshot. A cancelled subscription,
    an uninstalled or disabled app, or a suspended app is not charged; a
    wallet that can't pay moves the subscription to ``past_due`` and access
    stops (the merchant tops up and subscribes again). Idempotent: the charge
    key is the period being bought. A trial ending is charged here like any
    renewal. Merchant notifications to send after commit go to ``notices``.
    """
    now = now or _now()
    stats = {"renewed": 0, "cancelled": 0, "past_due": 0}
    changed: set[UUID] = set()
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
        changed.add(sub.tenant_id)
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
        coupon = await _active_coupon(db, sub)
        q = await quote_for(db, app, sub.price_cents, coupon, sub_vat_bps(sub))
        tx = None
        try:
            if sub.price_cents:
                q, tx = await _charge(
                    db,
                    source,
                    app,
                    tenant_id=sub.tenant_id,
                    list_cents=sub.price_cents,
                    currency=sub.currency,
                    key=key,
                    note=f"{app.slug} ({sub.cycle}) renewal",
                    coupon=coupon,
                    vat_bps=sub_vat_bps(sub),
                )
        except (InsufficientFundsError, WalletSuspendedError):
            sub.status = "past_due"
            stats["past_due"] += 1
            logger.info("app_subscription_past_due", app=app.slug, sub=str(sub.id))
            if notices is not None:
                notices.append(
                    notice(
                        sub,
                        app,
                        "app.renewal_failed",
                        f"app-past-due:{sub.id}:{start.isoformat()}",
                        important=True,
                        link="/wallet",
                        amount_cents=q.total_cents,
                    )
                )
            continue
        sub.current_period_start = start
        sub.current_period_end = start + timedelta(days=CYCLE_DAYS[sub.cycle])
        sub.is_trial = False
        if tx is not None:
            if coupon:
                _consume_coupon(sub)
            await _book(db, app, sub, q, tx, key, now)
            stats["renewed"] += 1
            if notices is not None:
                notices.append(
                    notice(
                        sub,
                        app,
                        "app.renewal_charged",
                        f"app-renewal:{key}",
                        amount_cents=q.total_cents,
                        period_end=_aware(sub.current_period_end).isoformat(),
                    )
                )
    for tenant_id in changed:
        await TenantRepository(db).bump_entitlements_version(tenant_id)
    await db.flush()
    return stats


async def trial_ending_notices(
    db: AsyncSession, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Trials that end within 3 days and will be charged: warn once each."""
    now = now or _now()
    rows = (
        await db.execute(
            select(AppSubscriptionModel, AppModel)
            .join(AppModel, AppModel.id == AppSubscriptionModel.app_id)
            .where(
                AppSubscriptionModel.status == "active",
                AppSubscriptionModel.is_trial.is_(True),
                AppSubscriptionModel.cancel_at_period_end.is_(False),
                AppSubscriptionModel.current_period_end > now,
                AppSubscriptionModel.current_period_end <= now + TRIAL_WARNING,
            )
        )
    ).all()
    return [
        notice(
            sub,
            app,
            "app.trial_ending",
            f"app-trial-ending:{sub.id}",
            important=True,
            amount_cents=sub.price_cents,
            period_end=_aware(sub.current_period_end).isoformat(),
        )
        for sub, app in rows
    ]


async def usage_used_cents(db: AsyncSession, sub: AppSubscriptionModel) -> int:
    """Usage charged in the subscription's current period."""
    return int(
        await db.scalar(
            select(func.coalesce(func.sum(AppUsageRecordModel.amount_cents), 0)).where(
                AppUsageRecordModel.subscription_id == sub.id,
                AppUsageRecordModel.period_start == sub.current_period_start,
            )
        )
        or 0
    )


async def record_usage(
    db: AsyncSession,
    *,
    installation: AppInstallationModel,
    app: AppModel,
    source: ChargeSource,
    description: str,
    idempotency_key: str,
    amount_cents: int | None = None,
    units: int | None = None,
    now: datetime | None = None,
    notices: list[dict[str, Any]] | None = None,
) -> tuple[AppUsageRecordModel, bool]:
    """Charge one usage record to the wallet now. Returns ``(record, new)``.

    Charged immediately rather than summed at period end: nothing is owed
    that the wallet has not paid, a refused charge is visible to the app at
    once, and each record refunds like any other ``app_charge``. The same
    ``idempotency_key`` from the same installation returns the first record.
    Raises UsageError (``subscription_inactive``, ``usage_not_enabled``,
    ``invalid_amount``, ``usage_cap_exceeded``) or InsufficientFundsError.
    Caller owns the commit.
    """
    now = now or _now()
    sub = await subscription_for(db, installation.id, for_update=True)
    existing = await db.scalar(
        select(AppUsageRecordModel).where(
            AppUsageRecordModel.installation_id == installation.id,
            AppUsageRecordModel.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing, False
    if not paid_through(sub, now):
        raise UsageError(
            "subscription_inactive",
            "The store's subscription to this app is not active.",
        )
    if not sub.usage_cap_cents:
        raise UsageError("usage_not_enabled", "This subscription has no usage pricing.")
    if sub.usage_unit_cents:
        if not units or units < 1 or amount_cents is not None:
            raise UsageError("invalid_amount", "Report units, not an amount.")
        amount = units * sub.usage_unit_cents
    else:
        if not amount_cents or amount_cents < 1 or units is not None:
            raise UsageError("invalid_amount", "Report amount_cents, not units.")
        amount = amount_cents
    used = await usage_used_cents(db, sub)
    cap_notice = notice(
        sub,
        app,
        "app.usage_cap_reached",
        f"app-usage-cap:{sub.id}:{_aware(sub.current_period_start).isoformat()}",
        important=True,
        amount_cents=sub.usage_cap_cents,
    )
    if used + amount > sub.usage_cap_cents:
        if notices is not None:
            notices.append(cap_notice)
        raise UsageError(
            "usage_cap_exceeded",
            f"This charge would exceed the approved cap of {sub.usage_cap_cents} "
            f"piasters for the period ({used} used).",
        )
    key = f"app-usage:{installation.id}:{idempotency_key}"
    q, tx = await _charge(
        db,
        source,
        app,
        tenant_id=installation.tenant_id,
        list_cents=amount,
        currency=sub.currency,
        key=key,
        note=f"{app.slug} usage: {description}"[:255],
        vat_bps=sub_vat_bps(sub),
    )
    record = AppUsageRecordModel(
        tenant_id=installation.tenant_id,
        store_id=installation.store_id,
        app_id=app.id,
        installation_id=installation.id,
        subscription_id=sub.id,
        period_start=sub.current_period_start,
        units=units,
        amount_cents=amount,
        description=description,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    db.add(record)
    await db.flush()
    if tx is not None:
        await _book(db, app, sub, q, tx, key, now)
    if used + amount == sub.usage_cap_cents and notices is not None:
        notices.append(cap_notice)
    return record, True


async def refund_charge(
    db: AsyncSession, *, charge_id: UUID, actor_user_id: UUID, note: str
) -> tuple[WalletTransactionModel, PartnerLedgerEntryModel | None] | None:
    """Refund one ``app_charge`` in full: credit the merchant's wallet
    (``app_charge_reversal``, VAT included), take back the partner's share of
    it (a negative ``adjustment``) and issue a credit note against NUMU's fee
    invoice. None when it was already refunded. Raises
    LookupError for anything that is not an app charge. Caller commits, then
    invalidates the wallet cache."""
    charge = await db.get(WalletTransactionModel, charge_id)
    if charge is None or charge.kind != WalletTransactionKind.APP_CHARGE.value:
        raise LookupError("App charge not found")
    reversal = await WalletService(db).apply_entry(
        tenant_id=charge.tenant_id,
        kind=WalletTransactionKind.APP_CHARGE_REVERSAL,
        amount_cents=-charge.amount_cents,
        currency=charge.currency,
        idempotency_key=f"app-refund:{charge.id}",
        actor_user_id=actor_user_id,
        note=note,
        meta={"charge_id": str(charge.id)},
    )
    if reversal is None:
        return None
    invoice = await db.scalar(
        select(AppFeeInvoiceModel).where(
            AppFeeInvoiceModel.wallet_transaction_id == charge.id,
            AppFeeInvoiceModel.kind == "invoice",
        )
    )
    if invoice is not None:
        await issue_fee_invoice(
            db,
            tx=reversal,
            kind="credit_note",
            q=Quote(
                list_cents=invoice.list_price_cents,
                share_bps=invoice.share_bps,
                fee_cents=invoice.fee_cents,
                vat_cents=invoice.vat_cents,
                discount_cents=invoice.discount_cents,
                vat_bps=invoice.vat_bps,
            ),
            store_id=invoice.store_id,
            app_id=invoice.app_id,
            description=invoice.description,
            now=_now(),
            original=invoice,
        )
    sale = await db.scalar(
        select(PartnerLedgerEntryModel).where(
            PartnerLedgerEntryModel.idempotency_key == charge.idempotency_key,
            PartnerLedgerEntryModel.kind == "sale",
        )
    )
    if sale is None:
        return reversal, None
    reference = f"refund:{charge.id}"
    entry = PartnerLedgerEntryModel(
        partner_id=sale.partner_id,
        kind="adjustment",
        amount_cents=-sale.amount_cents,
        gross_cents=-(sale.gross_cents or 0),
        platform_fee_cents=-(sale.platform_fee_cents or 0),
        share_bps=sale.share_bps,
        discount_cents=-(sale.discount_cents or 0),
        vat_cents=-(sale.vat_cents or 0),
        currency=sale.currency,
        app_id=sale.app_id,
        subscription_id=sale.subscription_id,
        idempotency_key=reference,
        reference=reference,
        actor_user_id=actor_user_id,
        note=note,
    )
    db.add(entry)
    await db.flush()
    return reversal, entry


async def _credit_partner(
    db: AsyncSession,
    app: AppModel,
    sub: AppSubscriptionModel,
    q: Quote,
    currency: str,
    key: str,
    collected_at: datetime,
) -> PartnerLedgerEntryModel | None:
    """The partner's share of one charge at the rate it was quoted with,
    less their coupon. NUMU Apps: nothing to credit."""
    if app.developer_id is None:
        return None
    if q.partner_id is None:
        # Should not happen: publishing needs an approved partner. NUMU holds
        # the money until an admin records an adjustment.
        logger.error("partner_ledger_no_partner_account", app=app.slug, key=key)
        return None
    entry = PartnerLedgerEntryModel(
        partner_id=q.partner_id,
        kind="sale",
        amount_cents=q.partner_cents,
        gross_cents=q.list_cents - q.discount_cents,
        platform_fee_cents=q.fee_cents,
        share_bps=q.share_bps,
        discount_cents=q.discount_cents,
        vat_cents=q.vat_cents,
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


async def app_labels(
    db: AsyncSession, app_ids: list[UUID | None]
) -> dict[UUID, dict[str, str]]:
    """``{app_id: {"name", "slug"}}`` for ledger rows, which store only the id."""
    ids = {i for i in app_ids if i}
    if not ids:
        return {}
    rows = await db.execute(
        select(AppModel.id, AppModel.name, AppModel.slug).where(AppModel.id.in_(ids))
    )
    return {r.id: {"name": r.name, "slug": r.slug} for r in rows}


async def charge_ids(db: AsyncSession, keys: list[str]) -> dict[str, str]:
    """``{idempotency_key: wallet charge id}``: a sale shares its key with
    the ``app_charge`` it came from, which is what a refund names."""
    if not keys:
        return {}
    rows = await db.execute(
        select(WalletTransactionModel.idempotency_key, WalletTransactionModel.id).where(
            WalletTransactionModel.idempotency_key.in_(keys),
            WalletTransactionModel.kind == WalletTransactionKind.APP_CHARGE.value,
        )
    )
    return {k: str(i) for k, i in rows}


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
    """What may be paid out now: the balance, minus sales and referral
    credits still inside the 30-day hold. Never more than the balance."""
    cutoff = (now or _now()) - HOLD
    held = int(
        await db.scalar(
            select(
                func.coalesce(func.sum(PartnerLedgerEntryModel.amount_cents), 0)
            ).where(
                PartnerLedgerEntryModel.partner_id == partner_id,
                PartnerLedgerEntryModel.kind.in_(("sale", "referral")),
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
            f"EGP {payable / 100:,.2f} is payable now (sales stay on hold for "
            "30 days); a payout can't exceed it."
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


MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """``YYYY-MM`` to ``[start, end)`` in UTC."""
    if not MONTH_RE.match(month):
        raise ValueError("month must be YYYY-MM")
    year, mon = int(month[:4]), int(month[5:])
    start = datetime(year, mon, 1, tzinfo=UTC)
    end = datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=UTC)
    return start, end


def is_refund(entry: PartnerLedgerEntryModel) -> bool:
    return entry.kind == "adjustment" and (entry.reference or "").startswith("refund:")


async def partner_statement(
    db: AsyncSession, partner_id: UUID, month: str
) -> dict[str, Any]:
    """One month of a partner's ledger. Every figure is signed as it moves
    the balance, so ``closing = opening + net_sales + referrals + refunds
    + adjustments + payouts``. Sales: what merchants paid before VAT (gross,
    after the partner's coupons), NUMU's fee and the partner's share (net) at
    the share each sale was booked with. Refunds reverse a sale's share.
    Coupon discounts and NUMU's VAT are informational: VAT is NUMU's, on its
    fee, and never touches the partner's balance."""
    start, end = month_bounds(month)
    opening = int(
        await db.scalar(
            select(
                func.coalesce(func.sum(PartnerLedgerEntryModel.amount_cents), 0)
            ).where(
                PartnerLedgerEntryModel.partner_id == partner_id,
                PartnerLedgerEntryModel.created_at < start,
            )
        )
        or 0
    )
    entries = (
        (
            await db.execute(
                select(PartnerLedgerEntryModel)
                .where(
                    PartnerLedgerEntryModel.partner_id == partner_id,
                    PartnerLedgerEntryModel.created_at >= start,
                    PartnerLedgerEntryModel.created_at < end,
                )
                .order_by(PartnerLedgerEntryModel.created_at)
            )
        )
        .scalars()
        .all()
    )
    sales = [e for e in entries if e.kind == "sale"]
    refunds = [e for e in entries if is_refund(e)]
    others = [e for e in entries if e.kind == "adjustment" and not is_refund(e)]
    payouts = [e for e in entries if e.kind == "payout"]
    referrals = [e for e in entries if e.kind == "referral"]
    apps = await app_labels(db, [e.app_id for e in entries])
    return {
        "month": month,
        "currency": "EGP",
        "opening_balance_cents": opening,
        "gross_sales_cents": sum(e.gross_cents or 0 for e in sales),
        "platform_fees_cents": sum(e.platform_fee_cents or 0 for e in sales),
        "net_sales_cents": sum(e.amount_cents for e in sales),
        "referrals_cents": sum(e.amount_cents for e in referrals),
        "refunds_cents": sum(e.amount_cents for e in refunds),
        "adjustments_cents": sum(e.amount_cents for e in others),
        "payouts_cents": sum(e.amount_cents for e in payouts),
        "coupon_discounts_cents": sum(e.discount_cents or 0 for e in sales + refunds),
        "vat_collected_cents": sum(e.vat_cents or 0 for e in sales + refunds),
        "closing_balance_cents": opening + sum(e.amount_cents for e in entries),
        "entries": [
            {
                "id": str(e.id),
                "kind": "refund" if is_refund(e) else e.kind,
                "amount_cents": e.amount_cents,
                "gross_cents": e.gross_cents,
                "platform_fee_cents": e.platform_fee_cents,
                "share_bps": e.share_bps,
                "discount_cents": e.discount_cents,
                "vat_cents": e.vat_cents,
                "app_name": apps.get(e.app_id, {}).get("name"),
                "app_slug": apps.get(e.app_id, {}).get("slug"),
                "reference": e.reference,
                "created_at": _aware(e.created_at).isoformat(),
            }
            for e in entries
        ],
    }


STATEMENT_TOTALS = (
    "opening_balance_cents",
    "gross_sales_cents",
    "platform_fees_cents",
    "net_sales_cents",
    "referrals_cents",
    "refunds_cents",
    "adjustments_cents",
    "payouts_cents",
    "coupon_discounts_cents",
    "vat_collected_cents",
    "closing_balance_cents",
)


def statement_csv(statement: dict[str, Any]) -> str:
    """The statement as CSV: the totals, a blank line, then every entry."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["month", statement["month"]])
    w.writerow(["currency", statement["currency"]])
    for k in STATEMENT_TOTALS:
        w.writerow([k, statement[k]])
    w.writerow([])
    cols = [
        "created_at",
        "kind",
        "app_slug",
        "gross_cents",
        "discount_cents",
        "share_bps",
        "platform_fee_cents",
        "vat_cents",
        "amount_cents",
        "reference",
    ]
    w.writerow(cols)
    for e in statement["entries"]:
        w.writerow([e[c] if e[c] is not None else "" for c in cols])
    return out.getvalue()


async def subscription_view(
    db: AsyncSession,
    sub: AppSubscriptionModel | None,
    app: AppModel,
    store_id: UUID,
) -> dict[str, Any]:
    """``subscription_out`` plus what needs the database: the trial still on
    offer and this period's usage."""
    price = app_price(app)
    usage = price.usage if price else None
    list_cents = (sub.price_cents if sub else None) or (
        price.price_cents if price else 0
    )
    coupon = await _active_coupon(db, sub)
    live = sub is not None and sub.status == "active"
    next_charge = (
        await quote_for(
            db, app, list_cents, coupon, sub_vat_bps(sub) if live else VAT_BPS
        )
        if price
        else None
    )
    return {
        **subscription_out(sub, app),
        "vat_bps": next_charge.vat_bps if next_charge else VAT_BPS,
        "next_charge": next_charge.out() if next_charge else None,
        "coupon": {
            "code": coupon.code,
            "cycles_left": sub.coupon_cycles_left,
        }
        if coupon
        else None,
        "trial_days": price.trial_days if price else 0,
        "trial_available": await trial_available(db, store_id, app),
        "is_trial": bool(sub and sub.is_trial),
        "usage": {
            "unit": usage["unit"],
            "unit_price_cents": (sub.usage_unit_cents if sub else None)
            or usage.get("price_cents"),
            "cap_cents": (sub.usage_cap_cents if sub else None) or usage["cap_cents"],
            "used_cents": await usage_used_cents(db, sub) if sub else 0,
        }
        if usage
        else None,
    }


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
