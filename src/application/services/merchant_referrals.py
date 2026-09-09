"""Applying a merchant-to-merchant referral code.

Extracted from `POST /referrals/apply` so the endpoint and the store-creation
path run the same code rather than two implementations of the same rules
drifting apart. The rules are the ones already in the route: a store can only
be referred once, nobody refers themselves, and the referrer's tier is
recomputed from how many referrals they already have.

Why store creation needs this at all: the merchant programme was complete
except that nothing ever called `apply`. The code generator, the shared link
(`numueg.app/signup?ref=CODE`), the listing endpoint and this redemption logic
all existed, and `merchant_referrals` had zero rows in production because no
path wrote to it — the hub's referrals page was reading a table nothing filled.

The gap is a timing one. A referral is tenant-to-tenant, and at registration
the referred merchant has no tenant yet; the store is created later. So the
code is parked on the lead at signup and redeemed here, once there is a tenant
to attach it to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.referral import MerchantReferralModel
from src.infrastructure.database.models.public.tenant import TenantModel

logger = get_logger(__name__)

#: Referral count → tier, and tier → commission rate. Kept here with the
#: application logic so a tier change cannot land in one caller and not the
#: other.
_TIERS = ((11, "diamond", 0.08), (6, "gold", 0.07), (3, "silver", 0.06))
_BRONZE = ("bronze", 0.05)

COMMISSION_WINDOW = timedelta(days=365)


def tier_for(count: int) -> tuple[str, float]:
    """The tier and rate a referrer earns at, given their referral count."""
    for threshold, name, rate in _TIERS:
        if count >= threshold:
            return name, rate
    return _BRONZE


async def resolve_referrer(db: AsyncSession, code: str) -> UUID | None:
    """The tenant behind a referral code, or None.

    Three lookups, narrowest first.

    1. An existing `merchant_referrals` row with this code — the cheapest
       answer, and the only one that works for the historical
       `STORENAME-NUMU-XXXX` codes minted before codes were persisted.
    2. The lead that owns this code, and the tenant it became. This is the
       path every code takes now: one code per merchant, stored on the lead,
       sent by the referral email and shown on the merchant's page.
    3. The old subdomain guess, kept only for codes issued by the previous
       unpersisted generator. It matches on a LIKE and can therefore match
       the wrong store, so it runs last and only when the first two find
       nothing — it is a compatibility shim, not a lookup.
    """
    referrer = (
        await db.execute(
            select(MerchantReferralModel.referrer_tenant_id)
            .where(MerchantReferralModel.referral_code == code)
            .limit(1)
        )
    ).scalar_one_or_none()
    if referrer:
        return referrer

    owner_tenant = (
        await db.execute(
            select(MerchantLeadModel.tenant_id)
            .where(
                MerchantLeadModel.referral_code == code,
                MerchantLeadModel.tenant_id.isnot(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if owner_tenant:
        return owner_tenant

    if "-NUMU-" not in code:
        return None
    prefix = code.split("-NUMU-")[0].strip().lower()
    if not prefix:
        return None
    return (
        await db.execute(
            select(TenantModel.id).where(TenantModel.subdomain.ilike(f"%{prefix}%"))
        )
    ).scalar_one_or_none()


async def apply_referral(
    db: AsyncSession, *, code: str, referred_tenant_id: UUID
) -> bool:
    """Record that `referred_tenant_id` was brought in by `code`.

    Returns True when a referral was created. Every "no" is a normal outcome —
    an unknown code, a self-referral, a store already referred — so none of
    them raise: this runs inside store creation, and a referral must never be
    the reason a merchant cannot open their shop.

    Does not commit; joins the caller's transaction.
    """
    if not code:
        return False
    try:
        existing = (
            await db.execute(
                select(MerchantReferralModel.id).where(
                    MerchantReferralModel.referred_tenant_id == referred_tenant_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            return False

        referrer_id = await resolve_referrer(db, code)
        # Self-referral is the first thing anyone tries.
        if referrer_id is None or referrer_id == referred_tenant_id:
            return False

        count = (
            await db.execute(
                select(func.count(MerchantReferralModel.id)).where(
                    MerchantReferralModel.referrer_tenant_id == referrer_id
                )
            )
        ).scalar() or 0
        _tier, rate = tier_for(count + 1)

        db.add(
            MerchantReferralModel(
                referrer_tenant_id=referrer_id,
                referred_tenant_id=referred_tenant_id,
                referral_code=code,
                status="pending",
                commission_rate=rate,
                commission_expires_at=datetime.now(UTC) + COMMISSION_WINDOW,
            )
        )
        logger.info(
            "referral_applied",
            referrer_tenant_id=str(referrer_id),
            referred_tenant_id=str(referred_tenant_id),
            code=code,
        )
        return True
    except Exception:
        logger.warning("referral_apply_failed", exc_info=True)
        return False


async def code_for_tenant(db: AsyncSession, tenant_id: UUID) -> str | None:
    """THE referral code for a merchant. One code, persisted, shared.

    There used to be two. `merchant_leads.referral_code` is minted by
    `ensure_referral_code`, stored, uniquely indexed, and is what the
    marketing referral email sends. The merchant's own page minted a second
    one — `STORENAME-NUMU-XXXX` — and never saved it, so a fresh random
    suffix came back on every request: a merchant who shared their link on
    Monday had a different code by Tuesday, and the Monday link named
    something that existed nowhere. That is what the subdomain LIKE fallback
    in `resolve_referrer` was compensating for.

    Both programmes now key off the same string: the email sends it, the page
    shows it, lead attribution resolves it, and store creation redeems it.

    Returns None when the tenant has no lead row — merchants created by an
    admin, or before leads shipped. The caller decides what to show.
    """
    from src.application.services import referral_service

    # ORM, not raw SQL with a hardcoded `public.` prefix: the tests run on
    # SQLite, which has no schemas, and the literal string worked only on
    # Postgres.
    lead_id = (
        await db.execute(
            select(MerchantLeadModel.id)
            .where(MerchantLeadModel.tenant_id == tenant_id)
            .order_by(MerchantLeadModel.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if lead_id is None:
        return None
    return await referral_service.ensure_referral_code(db, lead_id)
