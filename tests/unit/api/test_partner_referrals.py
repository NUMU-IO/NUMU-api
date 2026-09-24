"""Partner referrals and the public partner directory.

Referrals: first touch wins, a partner earns only inside their window after
the merchant's first paid invoice, each invoice credits once, and a partner
sees only their own referred stores. Directory: only approved, opted-in,
non-hidden partners, and only while the program is open.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api.dependencies.partners import partner_context
from src.api.v1.routes import partner_portal as portal
from src.api.v1.routes.public import partners as directory
from src.application.services.merchant_leads import attach_tenant_to_lead
from src.application.services.partner_referrals import (
    attribute_tenant,
    credit_invoice,
    ensure_code,
)
from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database.models.public.app_billing import (
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.billing import BillingInvoiceModel
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
    PartnerMemberModel,
    PartnerReferralModel,
)
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel

T0 = datetime(2026, 1, 10, tzinfo=UTC)


async def _user(s):
    u = UserModel(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        first_name="Test",
        last_name="User",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
    )
    s.add(u)
    await s.flush()
    return u


async def _partner(s, **kw):
    owner = await _user(s)
    p = PartnerAccountModel(
        id=uuid4(),
        user_id=owner.id,
        kind="company",
        display_name=kw.pop("display_name", "Acme"),
        country="EG",
        support_email=owner.email,
        status=kw.pop("status", "approved"),
        agreement_version="2026-09-draft",
        **kw,
    )
    s.add(p)
    await s.flush()
    await ensure_code(s, p)
    return p


async def _tenant(s):
    t = TenantModel(
        id=uuid4(),
        name="Shop",
        subdomain=f"shop-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state="active",
    )
    s.add(t)
    await s.flush()
    return t


async def _invoice(s, tenant, paid_at, amount=100_000):
    inv = BillingInvoiceModel(
        tenant_id=tenant.id,
        period_start=paid_at,
        period_end=paid_at + timedelta(days=30),
        amount_cents=amount,
        currency="EGP",
        status="paid",
        paid_at=paid_at,
    )
    s.add(inv)
    return await credit_invoice(s, inv)


async def _credits(s, partner):
    return (
        (
            await s.execute(
                select(PartnerLedgerEntryModel).where(
                    PartnerLedgerEntryModel.partner_id == partner.id,
                    PartnerLedgerEntryModel.kind == "referral",
                )
            )
        )
        .scalars()
        .all()
    )


# ─── Attribution ──────────────────────────────────────────────────


async def test_store_creation_attributes_the_partner_whose_link_brought_it(
    test_session,
):
    partner = await _partner(test_session)
    merchant, tenant = await _user(test_session), await _tenant(test_session)
    test_session.add(
        MerchantLeadModel(
            email=merchant.email,
            source="signup",
            user_id=merchant.id,
            referral_code_used=partner.referral_code,
        )
    )
    await test_session.flush()

    await attach_tenant_to_lead(
        test_session, user_id=merchant.id, tenant_id=tenant.id, subdomain="shop"
    )

    ref = await test_session.scalar(
        select(PartnerReferralModel).where(PartnerReferralModel.tenant_id == tenant.id)
    )
    assert ref.partner_id == partner.id


async def test_first_touch_wins(test_session):
    first, second = await _partner(test_session), await _partner(test_session)
    tenant, merchant = await _tenant(test_session), await _user(test_session)
    kw = {"tenant_id": tenant.id, "user_id": merchant.id}
    assert await attribute_tenant(test_session, code=first.referral_code, **kw)
    assert not await attribute_tenant(test_session, code=second.referral_code, **kw)
    ref = await test_session.scalar(
        select(PartnerReferralModel).where(PartnerReferralModel.tenant_id == tenant.id)
    )
    assert ref.partner_id == first.id


async def test_no_self_referral_and_no_unapproved_partner(test_session):
    partner = await _partner(test_session)
    pending = await _partner(test_session, status="pending")
    tenant = await _tenant(test_session)
    assert not await attribute_tenant(
        test_session,
        code=partner.referral_code,
        tenant_id=tenant.id,
        user_id=partner.user_id,
    )
    assert not await attribute_tenant(
        test_session,
        code=pending.referral_code,
        tenant_id=tenant.id,
        user_id=uuid4(),
    )
    assert not await attribute_tenant(
        test_session, code="ABCDEFGH", tenant_id=tenant.id, user_id=uuid4()
    )


# ─── Commission ───────────────────────────────────────────────────


async def test_commission_is_twenty_percent_within_twelve_months_only(test_session):
    partner = await _partner(test_session)
    tenant = await _tenant(test_session)
    await attribute_tenant(
        test_session,
        code=partner.referral_code,
        tenant_id=tenant.id,
        user_id=uuid4(),
    )

    first = await _invoice(test_session, tenant, T0)
    month_11 = await _invoice(test_session, tenant, T0 + timedelta(days=335))
    month_13 = await _invoice(test_session, tenant, T0 + timedelta(days=396))

    assert first.amount_cents == 20_000
    assert first.tenant_id == tenant.id
    assert month_11 is not None
    assert month_13 is None
    assert len(await _credits(test_session, partner)) == 2


async def test_a_merchant_without_a_referrer_credits_nobody(test_session):
    tenant = await _tenant(test_session)
    assert await _invoice(test_session, tenant, T0) is None


async def test_the_same_invoice_credits_once(test_session):
    partner = await _partner(test_session)
    tenant = await _tenant(test_session)
    await attribute_tenant(
        test_session,
        code=partner.referral_code,
        tenant_id=tenant.id,
        user_id=uuid4(),
    )
    inv = BillingInvoiceModel(
        tenant_id=tenant.id,
        period_start=T0,
        period_end=T0 + timedelta(days=30),
        amount_cents=50_000,
        status="paid",
        paid_at=T0,
    )
    test_session.add(inv)
    assert await credit_invoice(test_session, inv) is not None
    assert await credit_invoice(test_session, inv) is None
    assert len(await _credits(test_session, partner)) == 1


async def test_rate_is_per_partner(test_session):
    partner = await _partner(test_session, referral_bps=1000, referral_months=1)
    tenant = await _tenant(test_session)
    await attribute_tenant(
        test_session,
        code=partner.referral_code,
        tenant_id=tenant.id,
        user_id=uuid4(),
    )
    assert (await _invoice(test_session, tenant, T0)).amount_cents == 10_000
    assert await _invoice(test_session, tenant, T0 + timedelta(days=31)) is None


# ─── Portal authorization ─────────────────────────────────────────


async def test_a_partner_sees_only_their_own_referrals(test_session):
    mine, theirs = await _partner(test_session), await _partner(test_session)
    t1, t2 = await _tenant(test_session), await _tenant(test_session)
    for p, t in ((mine, t1), (theirs, t2)):
        await attribute_tenant(
            test_session, code=p.referral_code, tenant_id=t.id, user_id=uuid4()
        )
    await _invoice(test_session, t1, T0)

    dev = await _user(test_session)
    test_session.add(
        PartnerMemberModel(
            partner_id=mine.id,
            user_id=dev.id,
            email=dev.email,
            role="developer",
            status="active",
        )
    )
    await test_session.flush()
    ctx = await partner_context(user=(dev.id, "store_owner"), db=test_session)

    out = (await portal.referrals(ctx=ctx, db=test_session)).data
    assert [s.tenant_id for s in out.stores] == [t1.id]
    assert out.earned_cents == 20_000
    assert out.link.endswith(mine.referral_code)


# ─── Directory ────────────────────────────────────────────────────


async def _open_program(s):
    s.add(PlatformConfigModel(key="partner_program", value={"enabled": True}))
    await s.flush()


async def test_directory_lists_only_opted_in_visible_approved_partners(test_session):
    await _open_program(test_session)
    shown = await _partner(
        test_session,
        display_name="Shown",
        directory_listed=True,
        verified=True,
        directory_profile={"services": ["setup"]},
    )
    await _partner(test_session, display_name="Not opted in")
    hidden = await _partner(test_session, directory_listed=True, directory_hidden=True)
    await _partner(test_session, directory_listed=True, status="suspended")

    out = (await directory.list_partners(db=test_session)).data
    assert [c.id for c in out] == [shown.id]
    assert out[0].badges == ["verified"]
    assert (await directory.list_partners(db=test_session, service="apps")).data == []
    with pytest.raises(HTTPException):
        await directory.get_partner(hidden.id, db=test_session)


async def test_directory_is_empty_while_the_program_is_closed(test_session):
    await _partner(test_session, directory_listed=True)
    assert (await directory.list_partners(db=test_session)).data == []
