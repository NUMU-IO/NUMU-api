"""Paid apps: per-partner share, VAT on NUMU's fee, partner coupons.

Same in-memory SQLite setup and helpers as test_app_billing.py.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api.dependencies.partners import PartnerContext
from src.api.v1.routes import partner_portal as portal
from src.application.services import app_billing as billing
from src.infrastructure.database.models.public.app import AppInstallationModel
from src.infrastructure.database.models.public.app_billing import (
    AppFeeInvoiceModel,
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.wallet import WalletTransactionModel
from tests.unit.application.test_app_billing import (
    NOW,
    PRICE,
    _balance,
    _ledger_sum,
    _seed,
    _source,
)


def _ctx(partner) -> PartnerContext:
    return PartnerContext(partner.user_id, partner.user_id, partner, "owner")


async def _subscribe(session, install, app, now=NOW, code=None):
    return await billing.subscribe(
        session,
        installation=install,
        app=app,
        source=_source(session),
        now=now,
        coupon_code=code,
    )


async def _coupon(session, partner, app, **kw):
    body = portal.AppCouponCreate(app_id=app.id, code=kw.pop("code", "launch"), **kw)
    return (await portal.create_coupon(body, _ctx(partner), session)).data


# ─── Rounding and VAT ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "bps", "expected"),
    [(1_980, 1_400, 277), (25, 1_400, 4), (24, 1_400, 3), (5, 5_000, 3), (0, 1_400, 0)],
)
def test_bps_of_rounds_half_up(amount, bps, expected):
    assert billing.bps_of(amount, bps) == expected


def test_vat_is_on_numus_fee_only_and_added_on_top():
    q = billing.quote(PRICE, 8_000)
    assert (q.fee_cents, q.vat_cents, q.partner_cents) == (1_980, 277, 7_920)
    assert q.total_cents == PRICE + 277
    numu_app = billing.quote(PRICE, 0)
    assert (numu_app.fee_cents, numu_app.vat_cents) == (PRICE, 1_386)


def test_a_coupon_is_capped_at_the_partner_share():
    q = billing.quote(PRICE, 8_000, discount_cents=PRICE)
    assert q.capped and q.discount_cents == 7_920 and q.partner_cents == 0
    assert (q.fee_cents, q.vat_cents) == (1_980, 277)
    assert q.total_cents == 1_980 + 277


# ─── Per-partner share ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_partners_own_share_is_used_and_recorded(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=50_000)
    partner.share_bps = 7_000
    await test_session.commit()

    await _subscribe(test_session, install, app)
    await test_session.commit()

    sale = await test_session.scalar(select(PartnerLedgerEntryModel))
    assert (sale.share_bps, sale.amount_cents, sale.platform_fee_cents) == (
        7_000,
        6_930,
        2_970,
    )
    assert sale.vat_cents == 416
    assert await _balance(test_session, tenant.id) == 50_000 - PRICE - 416

    invoice = await test_session.scalar(select(AppFeeInvoiceModel))
    assert invoice.number == f"NUMU-{NOW.year}-000001"
    assert (invoice.fee_cents, invoice.vat_cents, invoice.share_bps) == (
        2_970,
        416,
        7_000,
    )
    assert invoice.total_cents == PRICE + 416

    # A later change applies to the next charge; the booked sale keeps its rate.
    partner.share_bps = None
    await test_session.commit()
    await billing.renew_due(
        test_session, source=_source(test_session), now=NOW + timedelta(days=31)
    )
    await test_session.commit()
    rates = (
        await test_session.scalars(
            select(PartnerLedgerEntryModel.share_bps).order_by(
                PartnerLedgerEntryModel.created_at
            )
        )
    ).all()
    assert rates == [7_000, 8_000]
    numbers = (
        await test_session.scalars(
            select(AppFeeInvoiceModel.number).order_by(AppFeeInvoiceModel.number)
        )
    ).all()
    assert numbers == [f"NUMU-{NOW.year}-000001", f"NUMU-{NOW.year}-000002"]


# ─── Refund ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refund_reverses_the_vat_with_a_credit_note(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=20_000)
    await _subscribe(test_session, install, app)
    await test_session.commit()
    charge = await test_session.scalar(
        select(WalletTransactionModel).where(
            WalletTransactionModel.kind == "app_charge"
        )
    )
    assert charge.amount_cents == -(PRICE + 277)

    for _ in range(2):
        await billing.refund_charge(
            test_session, charge_id=charge.id, actor_user_id=uuid4(), note="refund"
        )
    await test_session.commit()

    assert await _balance(test_session, tenant.id) == 20_000
    assert await _ledger_sum(test_session, partner.id) == 0
    notes = (
        await test_session.scalars(
            select(AppFeeInvoiceModel).where(AppFeeInvoiceModel.kind == "credit_note")
        )
    ).all()
    assert len(notes) == 1
    note = notes[0]
    assert note.number == f"NUMU-CN-{NOW.year}-000001"
    assert (note.fee_cents, note.vat_cents, note.total_cents) == (
        -1_980,
        -277,
        -(PRICE + 277),
    )
    adjustment = await test_session.scalar(
        select(PartnerLedgerEntryModel).where(
            PartnerLedgerEntryModel.kind == "adjustment"
        )
    )
    assert adjustment.vat_cents == -277 and adjustment.share_bps == 8_000
    st = await billing.partner_statement(
        test_session, partner.id, NOW.strftime("%Y-%m")
    )
    assert st["vat_collected_cents"] == 0
    assert "vat_collected_cents,0" in billing.statement_csv(st)


# ─── Coupons ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_coupon_discounts_the_partner_share_only_and_is_capped(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=20_000)
    created = await _coupon(test_session, partner, app, percent_off=100)
    assert created["capped"] and created["max_discount_cents"] == 7_920
    await test_session.commit()

    await _subscribe(test_session, install, app, code="LAUNCH")
    await test_session.commit()

    sale = await test_session.scalar(select(PartnerLedgerEntryModel))
    assert (sale.amount_cents, sale.discount_cents, sale.platform_fee_cents) == (
        0,
        7_920,
        1_980,
    )
    assert await _balance(test_session, tenant.id) == 20_000 - 1_980 - 277
    st = await billing.partner_statement(
        test_session, partner.id, NOW.strftime("%Y-%m")
    )
    assert st["coupon_discounts_cents"] == 7_920 and st["vat_collected_cents"] == 277


@pytest.mark.asyncio
async def test_a_first_cycle_coupon_leaves_renewals_at_full_price(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=50_000)
    await _coupon(test_session, partner, app, amount_off_cents=1_000, duration_cycles=1)
    await test_session.commit()
    sub, _ = await _subscribe(test_session, install, app, code="launch")
    await test_session.commit()
    assert sub.coupon_id is None
    await billing.renew_due(
        test_session, source=_source(test_session), now=NOW + timedelta(days=31)
    )
    await test_session.commit()
    shares = (
        await test_session.scalars(
            select(PartnerLedgerEntryModel.amount_cents).order_by(
                PartnerLedgerEntryModel.created_at
            )
        )
    ).all()
    assert shares == [7_920 - 1_000, 7_920]
    assert await _balance(test_session, tenant.id) == 50_000 - 2 * (PRICE + 277) + 1_000


@pytest.mark.asyncio
async def test_one_redemption_per_store_and_the_limit_holds(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=50_000)
    await _coupon(test_session, partner, app, percent_off=10, max_redemptions=1)
    await test_session.commit()
    await _subscribe(test_session, install, app, code="launch")
    await test_session.commit()

    store_id, tenant_id = install.store_id, tenant.id
    await test_session.delete(install)
    await test_session.commit()
    again = AppInstallationModel(
        tenant_id=tenant.id,
        store_id=store_id,
        app_id=app.id,
        is_enabled=True,
        settings={},
        status="active",
        granted_scopes=[],
    )
    test_session.add(again)
    await test_session.commit()
    with pytest.raises(billing.CouponError) as exc:
        await _subscribe(test_session, again, app, code="launch")
    assert exc.value.code == "coupon_used"
    await test_session.rollback()
    await test_session.refresh(app)

    other = AppInstallationModel(
        tenant_id=tenant_id,
        store_id=uuid4(),
        app_id=app.id,
        is_enabled=True,
        settings={},
        status="active",
        granted_scopes=[],
    )
    test_session.add(other)
    await test_session.commit()
    with pytest.raises(billing.CouponError) as exc:
        await _subscribe(test_session, other, app, code="launch")
    assert exc.value.code == "coupon_exhausted"


# ─── Partner authorization ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_partner_manages_and_sees_only_their_own(test_session):
    _, app_a, install_a, partner_a = await _seed(test_session, balance=20_000)
    _, _, _, partner_b = await _seed(test_session)
    created = await _coupon(test_session, partner_a, app_a, percent_off=10)
    await _subscribe(test_session, install_a, app_a)
    await test_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _coupon(test_session, partner_b, app_a, percent_off=10)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await portal.update_coupon(
            UUID(created["id"]),
            portal.AppCouponUpdate(active=False),
            _ctx(partner_b),
            test_session,
        )
    assert exc.value.status_code == 404
    assert (await portal.list_coupons(_ctx(partner_b), test_session)).data == []

    mine = (
        await portal.list_subscriptions(partner_a.user_id, test_session, limit=100)
    ).data
    theirs = (
        await portal.list_subscriptions(partner_b.user_id, test_session, limit=100)
    ).data
    assert mine["counts"]["active"] == 1 and mine["total"] == 1
    assert theirs["total"] == 0


# ─── Grandfathered subscriptions ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_grandfathered_subscription_renews_without_vat(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=50_000)
    sub, _ = await _subscribe(test_session, install, app)
    sub.vat_grandfathered = True
    await test_session.commit()
    after_first = await _balance(test_session, tenant.id)
    assert after_first == 50_000 - PRICE - 277

    await billing.renew_due(
        test_session, source=_source(test_session), now=NOW + timedelta(days=31)
    )
    await test_session.commit()
    assert await _balance(test_session, tenant.id) == after_first - PRICE
    invoice = await test_session.scalar(
        select(AppFeeInvoiceModel).order_by(AppFeeInvoiceModel.number.desc())
    )
    assert (invoice.vat_cents, invoice.vat_bps, invoice.total_cents) == (0, 0, PRICE)
    assert await _ledger_sum(test_session, partner.id) == 2 * 7_920


@pytest.mark.asyncio
async def test_a_new_subscription_is_not_grandfathered(test_session):
    tenant, app, install, _ = await _seed(test_session, balance=20_000)
    sub, _ = await _subscribe(test_session, install, app)
    await test_session.commit()
    assert sub.vat_grandfathered is False
    assert await _balance(test_session, tenant.id) == 20_000 - PRICE - 277


@pytest.mark.asyncio
async def test_resubscribing_after_cancel_ends_the_grandfathering(test_session):
    tenant, app, install, _ = await _seed(test_session, balance=50_000)
    sub, _ = await _subscribe(test_session, install, app)
    sub.vat_grandfathered = True
    await billing.cancel(test_session, install.id)
    await test_session.commit()
    later = NOW + timedelta(days=31)
    await billing.renew_due(test_session, source=_source(test_session), now=later)
    await test_session.commit()
    assert sub.status == "cancelled"
    before = await _balance(test_session, tenant.id)

    sub, started = await _subscribe(test_session, install, app, now=later)
    await test_session.commit()
    assert started and sub.vat_grandfathered is False
    assert await _balance(test_session, tenant.id) == before - PRICE - 277
