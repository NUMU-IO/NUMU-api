"""The merchant referral programme.

A merchant who brings another merchant earns as that merchant grows, not the
moment they sign up. Paying on signup buys signups; paying on the referred
merchant's first order buys merchants, and the two are not the same thing —
the funnel already shows how many leads register and never sell.

WHY MILESTONES ARE CODE AND AMOUNTS ARE DATA
Each milestone is a CONDITION — "the referred lead has a `first_order_at`" —
and something has to evaluate it. A table of milestone rows an operator could
add to would let them promise a reward that no code path ever pays, which is
worse than not offering it. So the conditions live here, and only the money
lives in `referral_milestone_settings`, which is the half a human changes.

ACCRUAL IS IDEMPOTENT BY THE DATABASE, NOT BY THIS CODE.
`accrue_for_lead` is called from several places — the activation handler, the
milestone handler, an admin recalculate — because a milestone can be reached
through any of them. It never checks "have I already paid this"; the unique
constraint on (referred_lead_id, milestone) does, and `ON CONFLICT DO NOTHING`
makes a second call cost one statement and change nothing. Doing it the other
way — read, decide, insert — races with itself and pays twice.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.merchant_lead import (
    MerchantLeadModel,
)

logger = logging.getLogger(__name__)

# A code a merchant reads out loud or types into a phone. Unambiguous
# alphabet: no O/0, no I/1/l — a referral that fails because someone read a
# zero as an O costs both a reward and the merchant's trust in the programme.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


@dataclass(frozen=True)
class Milestone:
    """One point at which a referrer earns.

    `column` is the lead column whose being non-NULL means the milestone was
    reached. Every one is a fill-only timestamp already maintained by the
    lead handlers, so this programme adds no new bookkeeping to keep in sync —
    it reads the funnel that already exists.
    """

    key: str
    label: str
    column: str
    description: str


#: Ordered as a merchant experiences them. Adding one here is the whole change
#: needed to offer it; its amount defaults to 0 until an operator sets it.
MILESTONES: tuple[Milestone, ...] = (
    Milestone(
        key="referred_registered",
        label="They created an account",
        column="registered_at",
        description="The referred merchant finished signing up.",
    ),
    Milestone(
        key="referred_store_created",
        label="They opened a store",
        column="store_created_at",
        description="The referred merchant created their first store.",
    ),
    Milestone(
        key="referred_first_product",
        label="They listed a product",
        column="first_product_at",
        description="The referred merchant added their first product.",
    ),
    Milestone(
        key="referred_first_order",
        label="They took their first order",
        column="first_order_at",
        description="The referred merchant received their first paid order.",
    ),
    Milestone(
        key="referred_first_commission",
        label="They started selling for real",
        column="first_commission_at",
        description="The referred merchant paid NUMU its first commission.",
    ),
)

MILESTONES_BY_KEY = {m.key: m for m in MILESTONES}

#: Whitelist for the interpolation below. Every value comes from this tuple,
#: never from a caller, so the accrual query cannot be steered by input.
_MILESTONE_COLUMNS = {m.key: m.column for m in MILESTONES}


def generate_referral_code() -> str:
    """A short code a merchant can share. Collisions are handled by the caller."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


async def ensure_referral_code(db: AsyncSession, lead_id: UUID) -> str | None:
    """Give a lead a code to share, if they do not have one. Never raises.

    THE code for that merchant: the referral email sends it, the merchant's
    own page shows it, lead attribution resolves it and store creation
    redeems it against the merchant referral programme.

    Retries on collision rather than pre-checking: the unique index is the
    authority, and a SELECT-then-INSERT would race with a second request for
    the same merchant.

    Written through the ORM rather than raw SQL. The previous version bound
    `str(lead_id)` against `public.merchant_leads` — correct only on Postgres,
    where UUID is native and `public` exists. SQLAlchemy stores UUIDs as
    CHAR(32) without dashes on SQLite, so the comparison never matched, and
    the blanket `except` below turned that into a silent None. The function
    had therefore never run under test, which is how the merchant page came to
    mint a second throwaway code of its own.
    """
    try:
        lead = (
            await db.execute(
                select(MerchantLeadModel).where(MerchantLeadModel.id == lead_id)
            )
        ).scalar_one_or_none()
        if lead is None:
            return None
        if lead.referral_code:
            return str(lead.referral_code)

        for _ in range(5):
            code = generate_referral_code()
            taken = (
                await db.execute(
                    select(MerchantLeadModel.id)
                    .where(MerchantLeadModel.referral_code == code)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if taken is not None:
                continue
            lead.referral_code = code
            await db.flush()
            return code
        logger.warning("referral_code_generation_exhausted lead_id=%s", lead_id)
        return None
    except Exception as exc:  # noqa: BLE001 — a code is never worth a 500
        logger.warning("referral_code_failed lead_id=%s error=%s", lead_id, exc)
        return None


async def accrue_for_lead(db: AsyncSession, lead_id: UUID) -> int:
    """Award every milestone this referred lead has reached. Returns how many.

    A no-op — one cheap statement — when the lead has no referrer, which is
    the overwhelmingly common case, so callers can invoke it unconditionally
    on any lead change without thinking about it.

    Runs in the CALLER's session and inside a savepoint: callers include the
    order-activation handler, and a referral bookkeeping failure must not roll
    back a merchant's order.
    """
    try:
        async with db.begin_nested():
            return await _accrue(db, lead_id)
    except Exception as exc:  # noqa: BLE001 — never break the producer
        logger.warning("referral_accrual_failed lead_id=%s error=%s", lead_id, exc)
        return 0


async def _accrue(db: AsyncSession, lead_id: UUID) -> int:
    lead = (
        (
            await db.execute(
                text(
                    "SELECT referred_by_lead_id, registered_at, store_created_at, "
                    "       first_product_at, first_order_at, first_commission_at "
                    "FROM public.merchant_leads WHERE id = :id"
                ),
                {"id": str(lead_id)},
            )
        )
        .mappings()
        .first()
    )

    if lead is None or lead["referred_by_lead_id"] is None:
        return 0

    settings = {
        row["milestone"]: row
        for row in (
            await db.execute(
                text(
                    "SELECT milestone, amount_cents, is_active "
                    "FROM public.referral_milestone_settings"
                )
            )
        )
        .mappings()
        .all()
    }

    awarded = 0
    for milestone in MILESTONES:
        setting = settings.get(milestone.key)
        # Unconfigured or switched off. Not an error: a milestone can exist in
        # code with no reward attached, which is how one is introduced before
        # anyone decides what it is worth.
        if setting is None or not setting["is_active"] or setting["amount_cents"] <= 0:
            continue
        if lead[_MILESTONE_COLUMNS[milestone.key]] is None:
            continue

        result = await db.execute(
            text(
                "INSERT INTO public.referral_rewards "
                "(referrer_lead_id, referred_lead_id, milestone, amount_cents) "
                "VALUES (:referrer, :referred, :milestone, :amount) "
                "ON CONFLICT (referred_lead_id, milestone) DO NOTHING"
            ),
            {
                "referrer": str(lead["referred_by_lead_id"]),
                "referred": str(lead_id),
                "milestone": milestone.key,
                "amount": int(setting["amount_cents"]),
            },
        )
        if result.rowcount:
            awarded += 1
            logger.info(
                "referral_reward_earned lead_id=%s milestone=%s amount=%s",
                lead_id,
                milestone.key,
                setting["amount_cents"],
            )

    if awarded:
        _notify(db, referrer_lead_id=lead["referred_by_lead_id"], count=awarded)
    return awarded


def _notify(db: AsyncSession, *, referrer_lead_id: UUID, count: int) -> None:
    from src.application.services import admin_notifications

    admin_notifications.notify(
        db,
        title="Referral reward earned",
        body=f"{count} milestone{'s' if count > 1 else ''} reached — a payout is waiting for approval.",
        url="/marketing",
        tag="admin:referral-rewards",
    )


async def accrue_for_tenant(db: AsyncSession, tenant_id: UUID) -> int:
    """Accrue for whichever lead belongs to *tenant_id*.

    The order and commission handlers know a tenant, not a lead. Leads are
    linked by tenant rather than store because a tenant with two stores is
    still one merchant.
    """
    row = (
        await db.execute(
            text(
                "SELECT id FROM public.merchant_leads "
                "WHERE tenant_id = :t ORDER BY created_at ASC LIMIT 1"
            ),
            {"t": str(tenant_id)},
        )
    ).first()
    if row is None:
        return 0
    return await accrue_for_lead(db, row[0])


async def recalculate_all(db: AsyncSession) -> dict[str, int]:
    """Sweep every referred lead. The safety net behind the live hooks.

    Live accrual fires from the lead handlers, so a milestone reached while
    those were deployed is already recorded. This exists for the rest: leads
    referred before a milestone's amount was configured, a handler that threw,
    a backfill. Idempotent, so running it twice changes nothing.
    """
    leads = (
        await db.execute(
            text(
                "SELECT id FROM public.merchant_leads "
                "WHERE referred_by_lead_id IS NOT NULL"
            )
        )
    ).all()

    awarded = 0
    for (lead_id,) in leads:
        awarded += await accrue_for_lead(db, lead_id)
    return {"leads_scanned": len(leads), "rewards_created": awarded}
