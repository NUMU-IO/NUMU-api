"""Partner referrals: a partner's link brings a merchant, and the partner earns
a share of that merchant's NUMU plan payments.

- **Attribution** rides the merchant referral path that already exists: the
  landing sends ``?ref=CODE`` with the registration, ``record_lead`` parks it
  on the lead, and store creation (``attach_tenant_to_lead``) calls
  ``attribute_tenant`` here first. A partner code is 10 characters, a lead's
  8, so the two never resolve to each other. First touch: a tenant that
  already has a referrer keeps it; only an admin reassigns.
- **Commission**: every paid plan invoice calls ``credit_invoice``. The
  partner gets ``referral_bps`` of it as a ``referral`` ledger entry while
  the invoice falls within ``referral_months`` of the tenant's first paid
  invoice. One entry per invoice (the ledger's idempotency key).
- **Payouts** are the existing manual ones (``record_payout``).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.referral_service import _CODE_ALPHABET
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app_billing import (
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.billing import BillingInvoiceModel
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
    PartnerReferralModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel

logger = get_logger(__name__)

CODE_PREFIX = "P"
CODE_LENGTH = 10
SIGNUP_LINK = "https://numueg.app/signup?ref="


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def window_end(first_paid_at: datetime, months: int) -> datetime:
    return _aware(first_paid_at) + timedelta(days=months * 365 // 12)


def commission(gross_cents: int, bps: int) -> int:
    return (gross_cents * bps + 5_000) // 10_000


async def ensure_code(db: AsyncSession, partner: PartnerAccountModel) -> str:
    """The partner's referral code, minted on first use."""
    while not partner.referral_code:
        code = CODE_PREFIX + "".join(
            secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH - 1)
        )
        taken = await db.scalar(
            select(PartnerAccountModel.id).where(
                PartnerAccountModel.referral_code == code
            )
        )
        if taken is None:
            partner.referral_code = code
            await db.flush()
    return partner.referral_code


async def attribute_tenant(
    db: AsyncSession, *, code: str, tenant_id: UUID, user_id: UUID
) -> bool:
    """Record that an approved partner's code brought ``tenant_id``.

    False for anything that is not a partner referral: an unknown code, a
    partner referring their own store, a tenant that already has a referrer.
    Never raises; joins the caller's transaction.
    """
    code = (code or "").strip().upper()
    if len(code) != CODE_LENGTH or not code.startswith(CODE_PREFIX):
        return False
    try:
        partner = (
            await db.execute(
                select(PartnerAccountModel).where(
                    PartnerAccountModel.referral_code == code,
                    PartnerAccountModel.status == "approved",
                )
            )
        ).scalar_one_or_none()
        if partner is None or partner.user_id == user_id:
            return False
        async with db.begin_nested():
            db.add(PartnerReferralModel(partner_id=partner.id, tenant_id=tenant_id))
            await db.flush()
        logger.info(
            "partner_referral_attributed",
            partner_id=str(partner.id),
            tenant_id=str(tenant_id),
        )
        return True
    except IntegrityError:
        return False
    except Exception:
        logger.warning("partner_referral_attribution_failed", exc_info=True)
        return False


async def credit_invoice(
    db: AsyncSession, invoice: BillingInvoiceModel
) -> PartnerLedgerEntryModel | None:
    """Credit the referring partner their share of one paid plan invoice.

    Idempotent per invoice. Never raises: a commission must not be the reason
    a merchant's payment fails to activate their plan. Caller owns the commit.
    """
    if not invoice.amount_cents or invoice.amount_cents <= 0:
        return None
    await db.flush()
    try:
        async with db.begin_nested():
            return await _credit(db, invoice)
    except IntegrityError:
        return None
    except Exception:
        logger.error(
            "partner_referral_credit_failed",
            invoice_id=str(invoice.id),
            exc_info=True,
        )
        return None


async def _credit(
    db: AsyncSession, invoice: BillingInvoiceModel
) -> PartnerLedgerEntryModel | None:
    referral = (
        await db.execute(
            select(PartnerReferralModel)
            .where(PartnerReferralModel.tenant_id == invoice.tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if referral is None:
        return None
    paid_at = _aware(invoice.paid_at or datetime.now(UTC))
    if referral.first_paid_at is None:
        referral.first_paid_at = paid_at
    partner = await db.get(PartnerAccountModel, referral.partner_id)
    if partner is None or paid_at >= window_end(
        referral.first_paid_at, partner.referral_months
    ):
        return None
    amount = commission(invoice.amount_cents, partner.referral_bps)
    if amount <= 0:
        return None
    entry = PartnerLedgerEntryModel(
        partner_id=partner.id,
        kind="referral",
        amount_cents=amount,
        gross_cents=invoice.amount_cents,
        currency=invoice.currency or "EGP",
        tenant_id=invoice.tenant_id,
        idempotency_key=f"referral:{invoice.id}",
        created_at=paid_at,
    )
    db.add(entry)
    await db.flush()
    return entry


async def assign(
    db: AsyncSession, *, tenant_id: UUID, partner_id: UUID | None
) -> PartnerReferralModel | None:
    """Admin override: set or clear a tenant's referrer. Earlier credits stay
    where they were; later invoices credit the new partner."""
    referral = (
        await db.execute(
            select(PartnerReferralModel).where(
                PartnerReferralModel.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if partner_id is None:
        if referral is not None:
            await db.delete(referral)
            await db.flush()
        return None
    if referral is None:
        first_paid = await db.scalar(
            select(func.min(BillingInvoiceModel.paid_at)).where(
                BillingInvoiceModel.tenant_id == tenant_id,
                BillingInvoiceModel.status == "paid",
                BillingInvoiceModel.amount_cents > 0,
            )
        )
        referral = PartnerReferralModel(tenant_id=tenant_id, first_paid_at=first_paid)
        db.add(referral)
    referral.partner_id = partner_id
    await db.flush()
    return referral


async def referred_stores(db: AsyncSession, partner_id: UUID) -> list[dict]:
    """The partner's referred merchants and what each has earned them."""
    earned = (
        select(
            PartnerLedgerEntryModel.tenant_id,
            func.sum(PartnerLedgerEntryModel.amount_cents).label("earned"),
        )
        .where(
            PartnerLedgerEntryModel.partner_id == partner_id,
            PartnerLedgerEntryModel.kind == "referral",
        )
        .group_by(PartnerLedgerEntryModel.tenant_id)
        .subquery()
    )
    rows = await db.execute(
        select(
            PartnerReferralModel,
            TenantModel.name,
            TenantModel.plan,
            TenantModel.lifecycle_state,
            earned.c.earned,
        )
        .join(TenantModel, TenantModel.id == PartnerReferralModel.tenant_id)
        .outerjoin(earned, earned.c.tenant_id == PartnerReferralModel.tenant_id)
        .where(PartnerReferralModel.partner_id == partner_id)
        .order_by(PartnerReferralModel.created_at.desc())
    )
    return [
        {
            "tenant_id": ref.tenant_id,
            "store_name": name,
            "signed_up_at": ref.created_at,
            "plan": plan,
            "status": str(state),
            "first_paid_at": ref.first_paid_at,
            "earned_cents": int(total or 0),
        }
        for ref, name, plan, state, total in rows
    ]
