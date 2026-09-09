"""Merchant referral redemption.

Two bugs met on the hub's referrals page, and neither surfaced as an error:

* `merchant_leads.referral_code` and `referred_by_lead_id` existed in the
  database but were never mapped on the model, so
  `lead.referred_by_lead_id = referrer` set a plain Python attribute and no
  UPDATE was ever emitted. Production read zero attributed leads.
* Nothing called `POST /referrals/apply`, so `merchant_referrals` had zero
  rows and the page listed a table no path wrote to.

These pin the parts that are cheap to get wrong again: the columns being
mapped at all, the tier boundaries, and every "no" that must not raise —
`apply_referral` runs inside store creation, where a referral must never be
the reason a merchant cannot open their shop.
"""

from __future__ import annotations

from uuid import uuid4

from src.application.services.merchant_referrals import apply_referral, tier_for
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.referral import MerchantReferralModel


def test_referral_columns_are_mapped():
    """The regression that made attribution a no-op."""
    columns = {c.name for c in MerchantLeadModel.__table__.columns}
    assert {
        "referral_code",
        "referred_by_lead_id",
        "referral_code_used",
    } <= columns


def test_tier_boundaries():
    assert tier_for(0) == ("bronze", 0.05)
    assert tier_for(2) == ("bronze", 0.05)
    assert tier_for(3) == ("silver", 0.06)
    assert tier_for(5) == ("silver", 0.06)
    assert tier_for(6) == ("gold", 0.07)
    assert tier_for(10) == ("gold", 0.07)
    assert tier_for(11) == ("diamond", 0.08)


async def test_empty_code_is_a_no_op(test_session):
    assert (
        await apply_referral(test_session, code="", referred_tenant_id=uuid4()) is False
    )


async def test_unknown_code_does_not_raise(test_session):
    """Store creation calls this. An unknown code must not cost a merchant
    their shop, so it returns False rather than raising."""
    applied = await apply_referral(
        test_session, code="NOSUCHSTORE-NUMU-9999", referred_tenant_id=uuid4()
    )
    assert applied is False
    assert await _count(test_session) == 0


async def test_referral_is_recorded_and_self_referral_refused(test_session):
    referrer, referred = uuid4(), uuid4()
    code = "VIONNE-NUMU-3D87"

    # Seed the code the way a first referral finds it: an existing row.
    test_session.add(
        MerchantReferralModel(
            referrer_tenant_id=referrer,
            referred_tenant_id=uuid4(),
            referral_code=code,
            status="pending",
            commission_rate=0.05,
        )
    )
    await test_session.flush()

    assert (
        await apply_referral(test_session, code=code, referred_tenant_id=referred)
        is True
    )

    # Same store twice: already referred.
    assert (
        await apply_referral(test_session, code=code, referred_tenant_id=referred)
        is False
    )

    # The first thing anyone tries.
    assert (
        await apply_referral(test_session, code=code, referred_tenant_id=referrer)
        is False
    )


async def _count(session) -> int:
    from sqlalchemy import func, select

    return (
        await session.execute(select(func.count(MerchantReferralModel.id)))
    ).scalar() or 0


async def test_one_code_per_merchant_and_it_is_stable(test_session):
    """The bug this reconciliation exists for.

    `/referrals/my-code` used to mint STORENAME-NUMU-XXXX with a random
    suffix and never persist it, so two calls returned two codes and the
    first link a merchant shared matched nothing. The code now comes from the
    lead row, where the referral email already reads it from.
    """
    from src.application.services.merchant_referrals import code_for_tenant

    tenant_id = uuid4()
    await _seed_lead(test_session, tenant_id)

    first = await code_for_tenant(test_session, tenant_id)
    second = await code_for_tenant(test_session, tenant_id)
    assert first, "a merchant with a lead gets a code"
    assert first == second, "asking twice must not mint a second code"


async def test_a_merchants_code_resolves_back_to_their_tenant(test_session):
    """One code, both programmes: the email sends it, and store creation
    redeems it against the tenant that owns it."""
    from src.application.services.merchant_referrals import (
        code_for_tenant,
        resolve_referrer,
    )

    referrer_tenant = uuid4()
    await _seed_lead(test_session, referrer_tenant)

    code = await code_for_tenant(test_session, referrer_tenant)
    assert await resolve_referrer(test_session, code) == referrer_tenant


async def test_legacy_store_named_codes_still_resolve(test_session):
    """Codes shared before this change have to keep working — a merchant's
    old link is out in the world and we do not get to invalidate it."""
    from src.application.services.merchant_referrals import resolve_referrer

    referrer = uuid4()
    test_session.add(
        MerchantReferralModel(
            referrer_tenant_id=referrer,
            referred_tenant_id=uuid4(),
            referral_code="VIONNE-NUMU-3D87",
            status="pending",
            commission_rate=0.05,
        )
    )
    await test_session.flush()
    assert await resolve_referrer(test_session, "VIONNE-NUMU-3D87") == referrer


async def _seed_lead(session, tenant_id):
    """A lead that has become a store.

    Through the ORM deliberately: a raw INSERT writes a dashed UUID string
    while SQLAlchemy's UUID type stores CHAR(32) without dashes on SQLite, so
    the row lands and nothing can ever match it.
    """
    from src.infrastructure.database.models.public.merchant_lead import (
        MerchantLeadModel,
    )

    session.add(
        MerchantLeadModel(
            email=f"{uuid4().hex}@example.com",
            source="signup",
            status="store_created",
            tenant_id=tenant_id,
        )
    )
    await session.flush()
