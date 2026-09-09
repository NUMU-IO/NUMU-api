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
