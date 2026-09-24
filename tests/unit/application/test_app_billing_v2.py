"""Paid apps v2: free trials, usage charges, refunds, partner statements.

Same in-memory SQLite setup and helpers as test_app_billing.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from src.api.v1.routes import app_usage, partners
from src.application.services import app_billing as billing
from src.application.services.app_manifest import Pricing, listing_pricing
from src.infrastructure.database.models.public.app import AppInstallationModel
from src.infrastructure.database.models.public.app_billing import (
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.wallet import WalletTransactionModel
from tests.unit.application.test_app_billing import (
    CHARGE,
    NOW,
    PRICE,
    _balance,
    _ledger_sum,
    _seed,
    _source,
)

TRIAL = {
    "plan": "recurring",
    "price_cents": PRICE,
    "cycle": "monthly",
    "trial_days": 14,
}
USAGE = {
    "plan": "usage",
    "usage": {"unit": {"ar": "رسالة", "en": "message"}, "cap_cents": 1_000},
}


async def _app_charges(session):
    return await session.scalar(
        select(func.count()).where(WalletTransactionModel.kind == "app_charge")
    )


async def _priced(session, pricing, **kw):
    tenant, app, install, partner = await _seed(session, **kw)
    app.manifest = {"pricing": pricing}
    await session.commit()
    return tenant, app, install, partner


async def _subscribe(session, install, app, now=NOW):
    return await billing.subscribe(
        session, installation=install, app=app, source=_source(session), now=now
    )


# ─── Manifest ──────────────────────────────────────────────────────


def test_trial_and_usage_pricing_validate_and_label():
    p = Pricing(model="recurring", price_cents=9_900, cycle="monthly", trial_days=14)
    stored = listing_pricing(p.model_dump(exclude_none=True))
    assert stored["trial_days"] == 14 and stored["plan"] == "recurring"
    assert stored["locales"]["en"]["label"] == "14-day free trial, then EGP 99 / month"
    assert stored["locales"]["ar"]["label"].startswith("تجربة مجانية ١٤ يوم ثم")
    usage = Pricing.model_validate({"model": "usage", "usage": USAGE["usage"]})
    assert (
        listing_pricing(usage.model_dump(exclude_none=True))["usage"]["cap_cents"]
        == 1_000
    )
    with pytest.raises(ValidationError):
        Pricing(model="free", trial_days=7)
    with pytest.raises(ValidationError):
        Pricing(model="usage")
    with pytest.raises(ValidationError):
        Pricing(model="recurring", price_cents=9_900, cycle="monthly", trial_days=91)


# ─── Trial ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_first_subscription_is_a_free_trial_then_renewal_charges(
    test_session,
):
    tenant, app, install, partner = await _priced(test_session, TRIAL, balance=CHARGE)
    sub, started = await _subscribe(test_session, install, app)
    await test_session.commit()

    assert started and sub.is_trial
    assert billing._aware(sub.current_period_end) == NOW + timedelta(days=14)
    assert await _balance(test_session, tenant.id) == CHARGE
    assert await _ledger_sum(test_session, partner.id) == 0
    assert await billing.is_entitled(test_session, install, app, now=NOW)

    notices: list = []
    warned = await billing.trial_ending_notices(
        test_session, now=NOW + timedelta(days=12)
    )
    assert [n["kind"] for n in warned] == ["app.trial_ending"]

    stats = await billing.renew_due(
        test_session,
        source=_source(test_session),
        now=NOW + timedelta(days=14, minutes=1),
        notices=notices,
    )
    await test_session.commit()
    assert stats["renewed"] == 1
    assert not sub.is_trial
    assert await _balance(test_session, tenant.id) == 0
    assert await _ledger_sum(test_session, partner.id) == 7_920
    assert [n["kind"] for n in notices] == ["app.renewal_charged"]


@pytest.mark.asyncio
async def test_one_trial_per_store_and_app_even_after_reinstalling(test_session):
    tenant, app, install, _ = await _priced(test_session, TRIAL, balance=CHARGE)
    await _subscribe(test_session, install, app)
    await test_session.commit()
    assert not await billing.trial_available(test_session, install.store_id, app)

    # Uninstall (the subscription goes with the row) and install again.
    store_id = install.store_id
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

    sub, started = await _subscribe(test_session, again, app)
    await test_session.commit()
    assert started and not sub.is_trial
    assert await _balance(test_session, tenant.id) == 0


@pytest.mark.asyncio
async def test_a_trial_that_runs_out_without_funds_goes_past_due(test_session):
    _, app, install, _ = await _priced(test_session, TRIAL)
    await _subscribe(test_session, install, app)
    notices: list = []
    stats = await billing.renew_due(
        test_session,
        source=_source(test_session),
        now=NOW + timedelta(days=15),
        notices=notices,
    )
    assert stats["past_due"] == 1
    assert notices[0]["kind"] == "app.renewal_failed"
    assert notices[0]["link"] == "/wallet" and notices[0]["important"]


# ─── Usage ─────────────────────────────────────────────────────────


async def _usage(session, install, app, key, amount, notices=None):
    return await billing.record_usage(
        session,
        installation=install,
        app=app,
        source=_source(session),
        amount_cents=amount,
        description="SMS batch",
        idempotency_key=key,
        now=NOW + timedelta(hours=1),
        notices=notices,
    )


@pytest.mark.asyncio
async def test_usage_is_charged_now_and_stops_at_the_approved_cap(test_session):
    tenant, app, install, partner = await _priced(test_session, USAGE, balance=5_000)
    sub, started = await _subscribe(test_session, install, app)
    assert started and sub.price_cents == 0 and sub.usage_cap_cents == 1_000
    assert await _app_charges(test_session) == 0

    await _usage(test_session, install, app, "a", 600)
    notices: list = []
    with pytest.raises(billing.UsageError) as exc:
        await _usage(test_session, install, app, "b", 500, notices)
    assert exc.value.code == "usage_cap_exceeded"
    assert [n["kind"] for n in notices] == ["app.usage_cap_reached"]
    await _usage(test_session, install, app, "c", 400)
    await test_session.commit()

    assert await billing.usage_used_cents(test_session, sub) == 1_000
    assert await _balance(test_session, tenant.id) == 5_000 - 1_028
    assert await _ledger_sum(test_session, partner.id) == 480 + 320

    # A new period starts from zero.
    await billing.renew_due(
        test_session, source=_source(test_session), now=NOW + timedelta(days=30)
    )
    assert await billing.usage_used_cents(test_session, sub) == 0


@pytest.mark.asyncio
async def test_a_replayed_usage_key_charges_once(test_session):
    tenant, app, install, partner = await _priced(test_session, USAGE, balance=5_000)
    await _subscribe(test_session, install, app)
    first, new = await _usage(test_session, install, app, "evt-1", 300)
    again, new_again = await _usage(test_session, install, app, "evt-1", 300)
    await test_session.commit()
    assert new and not new_again and first.id == again.id
    assert await _app_charges(test_session) == 1
    assert await _balance(test_session, tenant.id) == 5_000 - 308
    assert await _ledger_sum(test_session, partner.id) == 240


@pytest.mark.asyncio
async def test_usage_needs_an_active_subscription(test_session):
    _, app, install, _ = await _priced(test_session, USAGE, balance=5_000)
    with pytest.raises(billing.UsageError) as exc:
        await _usage(test_session, install, app, "x", 100)
    assert exc.value.code == "subscription_inactive"


@pytest.mark.asyncio
async def test_an_app_token_only_charges_its_own_installation(
    test_session, monkeypatch
):
    """Store B pays for the app; store A's token still gets 402: the
    installation comes from the token, never from the request."""
    _, app, install_a, _ = await _priced(test_session, USAGE, balance=5_000)
    tenant_b, _, _, _ = await _seed(test_session, balance=5_000)
    install_b = AppInstallationModel(
        tenant_id=tenant_b.id,
        store_id=uuid4(),
        app_id=app.id,
        is_enabled=True,
        settings={},
        status="active",
        granted_scopes=[],
    )
    test_session.add(install_b)
    await test_session.flush()
    await _subscribe(test_session, install_b, app)
    await test_session.commit()

    class _Session:
        async def __aenter__(self):
            return test_session

        async def __aexit__(self, *_exc):
            return False

    async def resolve(_session, raw):
        if raw != "numu_app_a":
            return None
        return SimpleNamespace(installation=install_a, app=app)

    monkeypatch.setattr(app_usage, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(app_usage, "resolve_app_token", resolve)
    body = app_usage.UsageChargeIn(
        amount_cents=100, description="x", idempotency_key="k"
    )

    with pytest.raises(HTTPException) as exc:
        await app_usage.create_usage_charge(body, authorization="Bearer numu_app_zz")
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        await app_usage.create_usage_charge(body, authorization="Bearer numu_app_a")
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "subscription_inactive"
    assert await _app_charges(test_session) == 0


# ─── Refunds ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refund_credits_the_wallet_and_reverses_the_share_once(test_session):
    tenant, app, install, partner = await _seed(test_session, balance=CHARGE)
    await _subscribe(test_session, install, app)
    await test_session.commit()
    charge = await test_session.scalar(
        select(WalletTransactionModel).where(
            WalletTransactionModel.kind == "app_charge"
        )
    )

    admin = uuid4()
    reversal, adjustment = await billing.refund_charge(
        test_session, charge_id=charge.id, actor_user_id=admin, note="Refund 14 days"
    )
    await test_session.commit()
    assert reversal.amount_cents == CHARGE
    assert adjustment.amount_cents == -7_920 and adjustment.gross_cents == -PRICE
    assert await _balance(test_session, tenant.id) == CHARGE
    assert await _ledger_sum(test_session, partner.id) == 0

    assert (
        await billing.refund_charge(
            test_session, charge_id=charge.id, actor_user_id=admin, note="again"
        )
        is None
    )
    await test_session.commit()
    assert await _balance(test_session, tenant.id) == CHARGE
    assert await _ledger_sum(test_session, partner.id) == 0
    with pytest.raises(LookupError):
        await billing.refund_charge(
            test_session, charge_id=reversal.id, actor_user_id=admin, note="x"
        )


# ─── Statements ────────────────────────────────────────────────────


def _entry(partner_id, kind, amount, when, **kw):
    return PartnerLedgerEntryModel(
        partner_id=partner_id,
        kind=kind,
        amount_cents=amount,
        currency="EGP",
        idempotency_key=f"{kind}:{uuid4()}",
        created_at=when,
        **kw,
    )


@pytest.mark.asyncio
async def test_statement_math(test_session):
    _, _, _, partner = await _seed(test_session)
    sep = datetime(2026, 9, 10, tzinfo=UTC)
    test_session.add_all([
        _entry(
            partner.id,
            "sale",
            7_920,
            datetime(2026, 8, 5, tzinfo=UTC),
            gross_cents=9_900,
            platform_fee_cents=1_980,
        ),
        _entry(
            partner.id, "sale", 7_920, sep, gross_cents=9_900, platform_fee_cents=1_980
        ),
        _entry(partner.id, "sale", 800, sep, gross_cents=1_000, platform_fee_cents=200),
        _entry(
            partner.id,
            "adjustment",
            -800,
            sep,
            reference="refund:abc",
            gross_cents=-1_000,
            platform_fee_cents=-200,
        ),
        _entry(partner.id, "adjustment", 100, sep, reference="goodwill"),
        _entry(partner.id, "payout", -5_000, sep, reference="CIB-1"),
        _entry(
            partner.id,
            "sale",
            7_920,
            datetime(2026, 10, 1, tzinfo=UTC),
            gross_cents=9_900,
            platform_fee_cents=1_980,
        ),
    ])
    await test_session.commit()

    st = await billing.partner_statement(test_session, partner.id, "2026-09")
    assert st["opening_balance_cents"] == 7_920
    assert (
        st["gross_sales_cents"],
        st["platform_fees_cents"],
        st["net_sales_cents"],
    ) == (
        10_900,
        2_180,
        8_720,
    )
    assert (st["refunds_cents"], st["adjustments_cents"], st["payouts_cents"]) == (
        -800,
        100,
        -5_000,
    )
    assert st["closing_balance_cents"] == 7_920 + 8_720 - 800 + 100 - 5_000
    assert len(st["entries"]) == 5
    assert "refund" in [e["kind"] for e in st["entries"]]
    csv_text = billing.statement_csv(st)
    assert "closing_balance_cents,10940" in csv_text
    with pytest.raises(ValueError):
        billing.month_bounds("2026-13")
    assert billing.month_bounds("2026-12")[1] == datetime(2027, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_partner_only_sees_their_own_statement(test_session):
    _, _, _, mine = await _seed(test_session)
    other = PartnerAccountModel(
        user_id=uuid4(),
        kind="company",
        display_name="Other Co",
        support_email="o@example.com",
        status="approved",
        country="EG",
    )
    test_session.add(other)
    await test_session.flush()
    when = datetime(2026, 9, 3, tzinfo=UTC)
    test_session.add_all([
        _entry(mine.id, "sale", 100, when, gross_cents=125, platform_fee_cents=25),
        _entry(other.id, "sale", 999, when, gross_cents=1_249, platform_fee_cents=250),
    ])
    await test_session.commit()

    st = await partners._my_statement(test_session, mine.user_id, "2026-09")
    assert st["net_sales_cents"] == 100 and len(st["entries"]) == 1
    with pytest.raises(HTTPException) as exc:
        await partners._my_statement(test_session, uuid4(), "2026-09")
    assert exc.value.status_code == 404
