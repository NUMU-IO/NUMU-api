"""Paid apps (apps plan, Phase 7): wallet charge, 80/20 ledger, renewals, access.

Runs on the in-memory SQLite engine (tests/conftest.py), so the wallet's
idempotency key and the ledger's unique key are exercised for real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.application.services import app_billing as billing
from src.application.services.wallet_service import WalletService
from src.core.entities.app import AppStatus
from src.core.entities.wallet import WalletTransactionKind
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.app_billing import (
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.wallet import WalletTransactionModel

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
PRICE = 9_900  # EGP 99 a month
CHARGE = PRICE + 277  # plus 14% VAT on NUMU's 20% fee (1_980)


def _pricing(price):
    if price is None:
        return {"pricing": {"plan": "free"}}
    return {
        "pricing": {
            "plan": "recurring",
            "price_cents": price,
            "cycle": "monthly",
            "currency": "EGP",
        }
    }


async def _seed(session, *, price=PRICE, partner=True, balance=0):
    tenant = TenantModel(
        id=uuid4(),
        name="Store Co",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state="active",
    )
    session.add(tenant)
    developer = uuid4() if partner else None
    partner_row = None
    if partner:
        partner_row = PartnerAccountModel(
            user_id=developer,
            kind="company",
            display_name="Bosta Sync Co",
            support_email="partner@example.com",
            status="approved",
            country="EG",
        )
        session.add(partner_row)
    app = AppModel(
        slug=f"paid-{uuid4().hex[:6]}",
        name="Paid App",
        developer_id=developer,
        status=AppStatus.PUBLISHED,
        manifest=_pricing(price),
    )
    session.add(app)
    await session.flush()
    install = AppInstallationModel(
        tenant_id=tenant.id,
        store_id=uuid4(),
        app_id=app.id,
        is_enabled=True,
        settings={},
        status="active",
        granted_scopes=[],
    )
    session.add(install)
    await session.flush()
    if balance:
        await _top_up(session, tenant.id, balance)
    await session.commit()
    return tenant, app, install, partner_row


async def _top_up(session, tenant_id, cents):
    await WalletService(session, cache=None).apply_entry(
        tenant_id=tenant_id,
        kind=WalletTransactionKind.TOPUP,
        amount_cents=cents,
        idempotency_key=f"seed:{uuid4()}",
    )


async def _balance(session, tenant_id):
    wallet = await WalletService(session, cache=None).get_or_create_wallet(tenant_id)
    return wallet.balance_cents


async def _ledger_sum(session, partner_id):
    return int(
        await session.scalar(
            select(
                func.coalesce(func.sum(PartnerLedgerEntryModel.amount_cents), 0)
            ).where(PartnerLedgerEntryModel.partner_id == partner_id)
        )
    )


def _source(session):
    source = billing.WalletChargeSource(session)
    source._wallet = WalletService(session, cache=None)
    return source


# ─── Pure rules ────────────────────────────────────────────────────


@pytest.mark.parametrize("gross", [1, 3, 5, 99, 9_900, 12_345, 499_999, 10_000_000])
def test_the_split_is_80_20_and_always_adds_up(gross):
    partner, fee = billing.split(gross)
    assert partner + fee == gross
    assert abs(fee - gross * 0.2) <= 0.5


def test_free_and_external_apps_have_no_price():
    assert billing.app_price(AppModel(manifest={"pricing": {"plan": "free"}})) is None
    assert (
        billing.app_price(AppModel(manifest={"pricing": {"plan": "external"}})) is None
    )
    assert billing.app_price(AppModel(manifest={})) is None
    price = billing.app_price(AppModel(manifest=_pricing(PRICE)))
    assert price == billing.AppPrice(PRICE, "EGP", "monthly")


# ─── Subscribe ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribing_charges_the_wallet_once_and_credits_the_partner_80(
    test_session,
):
    tenant, app, install, partner = await _seed(test_session, balance=20_000)

    sub, charged = await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=_source(test_session),
        now=NOW,
    )
    await test_session.commit()

    assert charged is True
    assert sub.status == "active"
    assert billing._aware(sub.current_period_end) == NOW + timedelta(days=30)
    assert await _balance(test_session, tenant.id) == 20_000 - CHARGE
    entry = await test_session.scalar(select(PartnerLedgerEntryModel))
    assert (
        entry.kind,
        entry.amount_cents,
        entry.gross_cents,
        entry.platform_fee_cents,
    ) == (
        "sale",
        7_920,
        PRICE,
        1_980,
    )
    assert await _ledger_sum(test_session, partner.id) == 7_920


@pytest.mark.asyncio
async def test_a_second_click_while_covered_charges_nothing(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=50_000)
    source = _source(test_session)
    await billing.subscribe(
        test_session, installation=install, app=app, source=source, now=NOW
    )
    _, charged = await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=source,
        now=NOW + timedelta(minutes=1),
    )
    await test_session.commit()

    assert charged is False
    assert await _balance(test_session, tenant.id) == 50_000 - CHARGE
    charges = await test_session.scalar(
        select(func.count()).where(WalletTransactionModel.kind == "app_charge")
    )
    assert charges == 1
    assert await _ledger_sum(test_session, partner.id) == 7_920


@pytest.mark.asyncio
async def test_an_empty_wallet_is_refused_and_nothing_is_written(test_session):
    _, app, install, partner = await _seed(test_session, balance=CHARGE - 1)
    install_id, partner_id = install.id, partner.id  # expired by the rollback
    with pytest.raises(billing.InsufficientFundsError) as exc:
        await billing.subscribe(
            test_session,
            installation=install,
            app=app,
            source=_source(test_session),
            now=NOW,
        )
    assert (exc.value.needed_cents, exc.value.balance_cents) == (CHARGE, CHARGE - 1)
    await test_session.rollback()
    assert await billing.subscription_for(test_session, install_id) is None
    assert await _ledger_sum(test_session, partner_id) == 0


@pytest.mark.asyncio
async def test_a_numu_app_keeps_the_whole_charge(test_session):
    tenant, app, install, _ = await _seed(
        test_session, partner=False, balance=PRICE + 1_386
    )
    await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=_source(test_session),
        now=NOW,
    )
    await test_session.commit()
    assert await _balance(test_session, tenant.id) == 0
    assert (
        await test_session.scalar(select(func.count(PartnerLedgerEntryModel.id))) == 0
    )


@pytest.mark.asyncio
async def test_a_free_app_cannot_be_subscribed_to(test_session):
    _, app, install, _ = await _seed(test_session, price=None)
    with pytest.raises(billing.NotPaidError):
        await billing.subscribe(
            test_session,
            installation=install,
            app=app,
            source=_source(test_session),
            now=NOW,
        )


# ─── Access ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_access_follows_the_paid_period(test_session):
    _, app, install, _ = await _seed(test_session, balance=CHARGE)
    assert not await billing.is_entitled(test_session, install, app, now=NOW)

    await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=_source(test_session),
        now=NOW,
    )
    await test_session.commit()
    assert await billing.is_entitled(test_session, install, app, now=NOW)
    assert await billing.is_entitled(
        test_session, install, app, now=NOW + timedelta(days=29)
    )
    # Partner Agreement § 11.5: 3 days' grace after the paid period.
    assert await billing.is_entitled(
        test_session, install, app, now=NOW + timedelta(days=32)
    )
    assert not await billing.is_entitled(
        test_session, install, app, now=NOW + timedelta(days=33, seconds=1)
    )


@pytest.mark.asyncio
async def test_a_free_app_is_always_entitled(test_session):
    _, app, install, _ = await _seed(test_session, price=None)
    assert await billing.is_entitled(test_session, install, app, now=NOW)


@pytest.mark.asyncio
async def test_cancelling_keeps_access_to_the_end_of_the_period(test_session):
    _, app, install, _ = await _seed(test_session, balance=CHARGE)
    await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=_source(test_session),
        now=NOW,
    )
    await billing.cancel(test_session, install.id)
    await test_session.commit()
    assert await billing.is_entitled(
        test_session, install, app, now=NOW + timedelta(days=10)
    )
    assert not await billing.is_entitled(
        test_session, install, app, now=NOW + timedelta(days=30, seconds=1)
    )


# ─── Renewal ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_renewal_charges_the_snapshot_price_and_credits_again(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=30_000)
    source = _source(test_session)
    await billing.subscribe(
        test_session, installation=install, app=app, source=source, now=NOW
    )
    await test_session.commit()
    # The partner raises the price; the existing subscriber keeps theirs.
    app.manifest = _pricing(19_900)
    await test_session.commit()

    later = NOW + timedelta(days=30, hours=1)
    stats = await billing.renew_due(test_session, source=source, now=later)
    await test_session.commit()

    assert stats == {"renewed": 1, "cancelled": 0, "past_due": 0}
    assert await _balance(test_session, tenant.id) == 30_000 - 2 * CHARGE
    sub = await billing.subscription_for(test_session, install.id)
    assert billing._aware(sub.current_period_end) == NOW + timedelta(days=60)
    assert await _ledger_sum(test_session, partner.id) == 2 * 7_920

    # Running the sweep again does nothing: the next period isn't due.
    again = await billing.renew_due(test_session, source=source, now=later)
    assert again == {"renewed": 0, "cancelled": 0, "past_due": 0}


@pytest.mark.asyncio
async def test_renewal_with_an_empty_wallet_goes_past_due(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=CHARGE)
    source = _source(test_session)
    await billing.subscribe(
        test_session, installation=install, app=app, source=source, now=NOW
    )
    await test_session.commit()

    later = NOW + timedelta(days=31)
    stats = await billing.renew_due(test_session, source=source, now=later)
    await test_session.commit()

    assert stats["past_due"] == 1
    assert await billing.is_entitled(test_session, install, app, now=later)  # grace
    assert not await billing.is_entitled(
        test_session, install, app, now=NOW + timedelta(days=34)
    )
    assert await _ledger_sum(test_session, partner.id) == 7_920

    # Top up and subscribe again: a fresh period from now.
    await _top_up(test_session, tenant.id, CHARGE)
    sub, charged = await billing.subscribe(
        test_session, installation=install, app=app, source=source, now=later
    )
    await test_session.commit()
    assert charged and sub.status == "active"
    assert await billing.is_entitled(test_session, install, app, now=later)


@pytest.mark.asyncio
async def test_a_cancelled_or_uninstalled_subscription_is_not_charged(test_session):
    tenant, app, install, _ = await _seed(test_session, balance=10 * PRICE)
    source = _source(test_session)
    await billing.subscribe(
        test_session, installation=install, app=app, source=source, now=NOW
    )
    await billing.cancel(test_session, install.id)
    await test_session.commit()

    stats = await billing.renew_due(
        test_session, source=source, now=NOW + timedelta(days=31)
    )
    await test_session.commit()
    assert stats["cancelled"] == 1
    assert await _balance(test_session, tenant.id) == 10 * PRICE - CHARGE


@pytest.mark.asyncio
async def test_a_suspended_app_is_not_charged(test_session):
    tenant, app, install, _ = await _seed(test_session, balance=10 * PRICE)
    source = _source(test_session)
    await billing.subscribe(
        test_session, installation=install, app=app, source=source, now=NOW
    )
    app.status = AppStatus.SUSPENDED
    await test_session.commit()

    stats = await billing.renew_due(
        test_session, source=source, now=NOW + timedelta(days=31)
    )
    await test_session.commit()
    assert stats["cancelled"] == 1
    assert await _balance(test_session, tenant.id) == 10 * PRICE - CHARGE


# ─── The partner ledger ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_balance_is_the_sum_of_the_entries(test_session):
    _, app, install, partner = await _seed(test_session, balance=10 * PRICE)
    source = _source(test_session)
    await billing.subscribe(
        test_session, installation=install, app=app, source=source, now=NOW
    )
    await billing.renew_due(test_session, source=source, now=NOW + timedelta(days=30))
    await test_session.commit()
    owed = await billing.partner_balance(test_session, partner.id)
    assert owed == 2 * 7_920 == await _ledger_sum(test_session, partner.id)

    await billing.record_payout(
        test_session,
        partner_id=partner.id,
        amount_cents=10_000,
        reference="CIB-778812",
        actor_user_id=uuid4(),
        now=NOW + timedelta(days=61),
    )
    await test_session.commit()
    after = await billing.partner_balance(test_session, partner.id)
    assert after == owed - 10_000 == await _ledger_sum(test_session, partner.id)


@pytest.mark.asyncio
async def test_a_payout_cannot_exceed_the_balance_or_repeat_a_reference(test_session):
    _, app, install, partner = await _seed(test_session, balance=CHARGE)
    await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=_source(test_session),
        now=NOW,
    )
    await test_session.commit()

    later = NOW + timedelta(days=31)
    with pytest.raises(ValueError, match="payable"):
        await billing.record_payout(
            test_session,
            partner_id=partner.id,
            amount_cents=7_921,
            reference="CIB-1",
            actor_user_id=uuid4(),
            now=later,
        )
    await billing.record_payout(
        test_session,
        partner_id=partner.id,
        amount_cents=1_000,
        reference="CIB-1",
        actor_user_id=uuid4(),
        now=later,
    )
    with pytest.raises(ValueError, match="already recorded"):
        await billing.record_payout(
            test_session,
            partner_id=partner.id,
            amount_cents=1_000,
            reference="CIB-1",
            actor_user_id=uuid4(),
            now=later,
        )
    await test_session.commit()
    assert await billing.partner_balance(test_session, partner.id) == 7_920 - 1_000


@pytest.mark.asyncio
async def test_sales_stay_on_hold_for_30_days(test_session):
    _, app, install, partner = await _seed(test_session, balance=CHARGE)
    await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=_source(test_session),
        now=NOW,
    )
    await test_session.commit()

    assert await billing.partner_balance(test_session, partner.id) == 7_920
    held = await billing.partner_payable(
        test_session, partner.id, now=NOW + timedelta(days=29)
    )
    assert held == 0
    with pytest.raises(ValueError, match="payable"):
        await billing.record_payout(
            test_session,
            partner_id=partner.id,
            amount_cents=1,
            reference="CIB-early",
            actor_user_id=uuid4(),
            now=NOW + timedelta(days=29),
        )
    free = await billing.partner_payable(
        test_session, partner.id, now=NOW + timedelta(days=30, seconds=1)
    )
    assert free == 7_920


@pytest.mark.asyncio
async def test_an_adjustment_reverses_a_refunded_share(test_session):
    _, app, install, partner = await _seed(test_session, balance=CHARGE)
    await billing.subscribe(
        test_session,
        installation=install,
        app=app,
        source=_source(test_session),
        now=NOW,
    )
    await billing.record_adjustment(
        test_session,
        partner_id=partner.id,
        amount_cents=-7_920,
        reference="refund-1001",
        note="Merchant refunded within 14 days",
        actor_user_id=uuid4(),
    )
    await test_session.commit()
    assert await billing.partner_balance(test_session, partner.id) == 0
    with pytest.raises(ValueError, match="already recorded"):
        await billing.record_adjustment(
            test_session,
            partner_id=partner.id,
            amount_cents=-1,
            reference="refund-1001",
            note="again",
            actor_user_id=uuid4(),
        )
    with pytest.raises(ValueError, match="non-zero"):
        await billing.record_adjustment(
            test_session,
            partner_id=partner.id,
            amount_cents=0,
            reference="refund-1002",
            note="zero",
            actor_user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_ledger_rows_can_name_their_app(test_session):
    _, app, _, _ = await _seed(test_session)
    labels = await billing.app_labels(test_session, [app.id, None, app.id])
    assert labels == {app.id: {"name": "Paid App", "slug": app.slug}}
    assert await billing.app_labels(test_session, [None]) == {}
