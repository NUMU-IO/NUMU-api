"""Admin: the Partner program queue, decisions and the program switch.

URL: /api/v1/admin/partners. Reading needs ``require_admin``. Every decision
(approve, reject, suspend, reinstate, open/close the program) needs the 2FA
step-up and is written to ``audit_logs``: approving a partner hands an
outsider a path to merchant data (plan 05 § 1.1).

Anything the partner reads (reject/suspend notes) is stored in ar + en.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.routes.partners import DEV_PLAN, PartnerAccountOut
from src.application.services.app_billing import partner_statement, statement_csv
from src.application.services.audit_service import AuditService
from src.application.services.partner_program import (
    program_enabled,
    set_program_enabled,
)
from src.application.services.partner_referrals import (
    assign,
    ensure_code,
    referred_stores,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel

router = APIRouter(
    prefix="/partners",
    tags=["Admin - Partners"],
    dependencies=[Depends(require_admin)],
)

_STEP_UP = [Depends(require_admin_2fa(max_age_seconds=300))]


class AdminPartner(PartnerAccountOut):
    user_id: UUID
    user_email: str
    email_verified: bool
    dev_store_count: int
    theme_count: int
    reviewed_by: UUID | None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    notes_ar: str | None = Field(default=None, max_length=2000)
    notes_en: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reject_needs_notes(self):
        if self.decision == "reject" and not (self.notes_ar and self.notes_en):
            raise ValueError("A rejection needs notes in Arabic and English.")
        return self


class SuspensionRequest(BaseModel):
    suspend: bool
    reason_ar: str | None = Field(default=None, max_length=2000)
    reason_en: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _suspend_needs_reason(self):
        if self.suspend and not (self.reason_ar and self.reason_en):
            raise ValueError("A suspension needs a reason in Arabic and English.")
        return self


class ProgramState(BaseModel):
    enabled: bool


async def _load(db: AsyncSession, partner_id: UUID) -> PartnerAccountModel:
    account = await db.get(PartnerAccountModel, partner_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    return account


async def _admin_view(db: AsyncSession, a: PartnerAccountModel) -> AdminPartner:
    user = await db.get(UserModel, a.user_id)
    dev_stores = await db.scalar(
        select(func.count(StoreModel.id))
        .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
        .where(StoreModel.owner_id == a.user_id, TenantModel.plan == DEV_PLAN)
    )
    themes = await db.scalar(
        select(func.count(MarketplaceThemeModel.id)).where(
            MarketplaceThemeModel.developer_id == a.user_id
        )
    )
    return AdminPartner(
        **PartnerAccountOut.model_validate(a, from_attributes=True).model_dump(),
        user_id=a.user_id,
        user_email=user.email if user else "",
        email_verified=bool(user and user.email_verified_at),
        dev_store_count=dev_stores or 0,
        theme_count=themes or 0,
        reviewed_by=a.reviewed_by,
    )


async def _audit(
    db: AsyncSession, admin_id: UUID, action: str, a: PartnerAccountModel, old: str
) -> None:
    await AuditService(db).log(
        event_type="admin.partner_decision",
        action=action,
        resource_type="partner_account",
        resource_id=str(a.id),
        user_id=admin_id,
        old_value={"status": old},
        new_value={"status": a.status, "notes": a.review_notes},
    )


# ─── Program switch ───────────────────────────────────────────────


@router.get("/program", response_model=SuccessResponse[ProgramState])
async def get_program(db: Annotated[AsyncSession, Depends(get_db)]):
    return SuccessResponse(data=ProgramState(enabled=await program_enabled(db)))


@router.put(
    "/program", response_model=SuccessResponse[ProgramState], dependencies=_STEP_UP
)
async def put_program(
    body: ProgramState,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Open or close the program. Closing hides every partner route (404);
    approved partners keep their accounts and dev stores."""
    old = await program_enabled(db)
    await set_program_enabled(db, body.enabled)
    await AuditService(db).log(
        event_type="admin.config_change",
        action="partner_program_open" if body.enabled else "partner_program_close",
        resource_type="platform_config",
        resource_id="partner_program",
        user_id=admin_id,
        old_value={"enabled": old},
        new_value={"enabled": body.enabled},
    )
    return SuccessResponse(data=body)


# ─── NUMU billing for Partner Apps (Partner Agreement § 11.1) ─────


@router.get("/billing", response_model=SuccessResponse[ProgramState])
async def get_partner_billing(db: Annotated[AsyncSession, Depends(get_db)]):
    from src.application.services.partner_program import partner_billing_enabled

    return SuccessResponse(data=ProgramState(enabled=await partner_billing_enabled(db)))


@router.put(
    "/billing", response_model=SuccessResponse[ProgramState], dependencies=_STEP_UP
)
async def put_partner_billing(
    body: ProgramState,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Allow ``recurring`` Partner Apps. Turn on only after counsel signs off
    and NUMU announces in writing that NUMU billing is live (Partner
    Agreement § 11.1). Off: partners may upload and publish only ``free`` or
    ``external`` apps; existing subscriptions keep renewing."""
    from src.application.services.partner_program import (
        partner_billing_enabled,
        set_partner_billing_enabled,
    )

    old = await partner_billing_enabled(db)
    await set_partner_billing_enabled(db, body.enabled)
    await AuditService(db).log(
        event_type="admin.config_change",
        action="partner_billing_on" if body.enabled else "partner_billing_off",
        resource_type="platform_config",
        resource_id="partner_billing",
        user_id=admin_id,
        old_value={"enabled": old},
        new_value={"enabled": body.enabled},
    )
    return SuccessResponse(data=body)


# ─── Queue ────────────────────────────────────────────────────────


@router.get("", response_model=SuccessResponse[list[AdminPartner]])
async def list_partners(
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[
        Literal["pending", "approved", "rejected", "suspended"] | None,
        Query(alias="status"),
    ] = None,
):
    """Oldest application first, so the queue is worked in order."""
    stmt = select(PartnerAccountModel).order_by(PartnerAccountModel.created_at)
    if status_filter:
        stmt = stmt.where(PartnerAccountModel.status == status_filter)
    rows = (await db.execute(stmt)).scalars().all()
    return SuccessResponse(data=[await _admin_view(db, a) for a in rows])


@router.get("/{partner_id}", response_model=SuccessResponse[AdminPartner])
async def get_partner(partner_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]):
    return SuccessResponse(data=await _admin_view(db, await _load(db, partner_id)))


@router.post(
    "/{partner_id}/decision",
    response_model=SuccessResponse[AdminPartner],
    dependencies=_STEP_UP,
)
async def decide(
    partner_id: UUID,
    body: DecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Approve or reject a pending application."""
    a = await _load(db, partner_id)
    if a.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Only a pending application can be decided ({a.status}).",
        )
    old = a.status
    a.status = "approved" if body.decision == "approve" else "rejected"
    a.review_notes = (
        {"ar": body.notes_ar, "en": body.notes_en}
        if body.notes_ar or body.notes_en
        else None
    )
    a.reviewed_by = admin_id
    a.reviewed_at = datetime.now(UTC)
    await _audit(db, admin_id, f"partner_{a.status}", a, old)
    await db.flush()
    return SuccessResponse(data=await _admin_view(db, a))


@router.post(
    "/{partner_id}/suspension",
    response_model=SuccessResponse[AdminPartner],
    dependencies=_STEP_UP,
)
async def suspend(
    partner_id: UUID,
    body: SuspensionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Suspend an approved partner, or reinstate a suspended one.

    Suspension closes ``require_approved_partner``: no uploads, submissions or
    new dev stores. Existing dev stores keep existing. Revoking installed app
    tokens is a separate choice that arrives with app tokens (Phase 4).
    """
    a = await _load(db, partner_id)
    wanted_from = "approved" if body.suspend else "suspended"
    if a.status != wanted_from:
        raise HTTPException(
            status_code=409,
            detail=f"Only an {wanted_from} partner can be "
            f"{'suspended' if body.suspend else 'reinstated'} ({a.status}).",
        )
    old = a.status
    a.status = "suspended" if body.suspend else "approved"
    a.review_notes = (
        {"ar": body.reason_ar, "en": body.reason_en} if body.suspend else None
    )
    a.reviewed_by = admin_id
    a.reviewed_at = datetime.now(UTC)
    await _audit(
        db,
        admin_id,
        "partner_suspended" if body.suspend else "partner_reinstated",
        a,
        old,
    )
    await db.flush()
    return SuccessResponse(data=await _admin_view(db, a))


# ─── Earnings (paid apps, Phase 7) ────────────────────────────────


class AdjustmentRequest(BaseModel):
    #: Signed piasters: negative reverses a refunded charge's share.
    amount_cents: int
    #: Unique per adjustment, e.g. the refund's reference.
    reference: str = Field(min_length=3, max_length=128)
    note: str = Field(min_length=3, max_length=500)


class PayoutRequest(BaseModel):
    #: Piasters already transferred to the partner's bank account.
    amount_cents: int = Field(gt=0)
    #: The bank transfer reference, unique per payout.
    reference: str = Field(min_length=3, max_length=128)
    note: str | None = Field(default=None, max_length=500)


def _entry_out(e, apps: dict | None = None) -> dict:
    app = (apps or {}).get(e.app_id) or {}
    return {
        "id": str(e.id),
        "kind": e.kind,
        "amount_cents": e.amount_cents,
        "gross_cents": e.gross_cents,
        "platform_fee_cents": e.platform_fee_cents,
        "currency": e.currency,
        "app_id": str(e.app_id) if e.app_id else None,
        "app_name": app.get("name"),
        "app_slug": app.get("slug"),
        "reference": e.reference,
        "note": e.note,
        "created_at": e.created_at,
    }


@router.get("/{partner_id}/ledger", response_model=SuccessResponse[dict])
async def ledger(
    partner_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """What NUMU owes this partner (the sum of every entry) and the latest
    entries: sales at the partner's 80% share, and payouts."""
    from src.application.services.app_billing import (
        app_labels,
        charge_ids,
        partner_balance,
        partner_payable,
    )
    from src.infrastructure.database.models.public.app_billing import (
        PartnerLedgerEntryModel,
    )

    await _load(db, partner_id)
    rows = (
        (
            await db.execute(
                select(PartnerLedgerEntryModel)
                .where(PartnerLedgerEntryModel.partner_id == partner_id)
                .order_by(PartnerLedgerEntryModel.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    apps = await app_labels(db, [e.app_id for e in rows])
    charges = await charge_ids(
        db, [e.idempotency_key for e in rows if e.kind == "sale"]
    )
    return SuccessResponse(
        data={
            "balance_cents": await partner_balance(db, partner_id),
            "payable_cents": await partner_payable(db, partner_id),
            "currency": "EGP",
            "entries": [
                {**_entry_out(e, apps), "charge_id": charges.get(e.idempotency_key)}
                for e in rows
            ],
        }
    )


@router.post(
    "/{partner_id}/payouts",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def payout(
    partner_id: UUID,
    body: PayoutRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Record a bank transfer ALREADY SENT to the partner. NUMU moves no money
    here; this keeps the balance true. Refuses more than is payable (sales
    stay on hold for 30 days) and a transfer reference already recorded."""
    from src.application.services.app_billing import partner_balance, record_payout

    a = await _load(db, partner_id)
    try:
        entry = await record_payout(
            db,
            partner_id=a.id,
            amount_cents=body.amount_cents,
            reference=body.reference,
            actor_user_id=admin_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await AuditService(db).log(
        event_type="admin.partner_program",
        action="partner_payout_recorded",
        resource_type="partner_account",
        resource_id=str(a.id),
        user_id=admin_id,
        new_value={"amount_cents": body.amount_cents, "reference": entry.reference},
    )
    await db.flush()
    return SuccessResponse(
        data={
            "entry": _entry_out(entry),
            "balance_cents": await partner_balance(db, a.id),
        }
    )


@router.post(
    "/{partner_id}/adjustments",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def adjustment(
    partner_id: UUID,
    body: AdjustmentRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """A signed correction to what NUMU owes the partner, e.g. reversing the
    partner's share of a charge refunded to a merchant (Partner Agreement
    § 11.3). The merchant's refund itself is a wallet adjustment."""
    from src.application.services.app_billing import partner_balance, record_adjustment

    a = await _load(db, partner_id)
    try:
        entry = await record_adjustment(
            db,
            partner_id=a.id,
            amount_cents=body.amount_cents,
            reference=body.reference,
            note=body.note,
            actor_user_id=admin_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await AuditService(db).log(
        event_type="admin.partner_program",
        action="partner_ledger_adjusted",
        resource_type="partner_account",
        resource_id=str(a.id),
        user_id=admin_id,
        new_value={"amount_cents": body.amount_cents, "reference": entry.reference},
    )
    await db.flush()
    return SuccessResponse(
        data={
            "entry": _entry_out(entry),
            "balance_cents": await partner_balance(db, a.id),
        }
    )


async def _statement(db: AsyncSession, partner_id: UUID, month: str) -> dict:
    await _load(db, partner_id)
    try:
        return await partner_statement(db, partner_id, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/{partner_id}/statements", response_model=SuccessResponse[dict])
async def admin_statement(
    partner_id: UUID, month: str, db: Annotated[AsyncSession, Depends(get_db)]
):
    """The partner's statement for one month (``YYYY-MM``), as they see it."""
    return SuccessResponse(data=await _statement(db, partner_id, month))


@router.get("/{partner_id}/statements/{month}.csv")
async def admin_statement_csv(
    partner_id: UUID, month: str, db: Annotated[AsyncSession, Depends(get_db)]
):
    return Response(
        content=statement_csv(await _statement(db, partner_id, month)),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="partner-{partner_id}-{month}.csv"'
            )
        },
    )


# ─── Referrals and the public directory ───────────────────────────


class ReferralTermsRequest(BaseModel):
    referral_bps: int = Field(ge=0, le=10_000)
    referral_months: int = Field(ge=1, le=60)


class AssignReferralRequest(BaseModel):
    subdomain: str = Field(min_length=1, max_length=63)


class DirectoryFlagsRequest(BaseModel):
    verified: bool | None = None
    directory_hidden: bool | None = None


async def _audit_change(
    db: AsyncSession, admin_id: UUID, action: str, a: PartnerAccountModel, old, new
) -> None:
    await AuditService(db).log(
        event_type="admin.partner_program",
        action=action,
        resource_type="partner_account",
        resource_id=str(a.id),
        user_id=admin_id,
        old_value=old,
        new_value=new,
    )


@router.get("/{partner_id}/referrals", response_model=SuccessResponse[dict])
async def list_referrals(
    partner_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]
):
    a = await _load(db, partner_id)
    return SuccessResponse(
        data={
            "code": await ensure_code(db, a),
            "referral_bps": a.referral_bps,
            "referral_months": a.referral_months,
            "stores": await referred_stores(db, a.id),
        }
    )


@router.put(
    "/{partner_id}/referral-terms",
    response_model=SuccessResponse[AdminPartner],
    dependencies=_STEP_UP,
)
async def put_referral_terms(
    partner_id: UUID,
    body: ReferralTermsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """This partner's referral share and window. Applies to invoices paid
    from now on; credits already written stay."""
    a = await _load(db, partner_id)
    old = {"referral_bps": a.referral_bps, "referral_months": a.referral_months}
    a.referral_bps, a.referral_months = body.referral_bps, body.referral_months
    await _audit_change(
        db, admin_id, "partner_referral_terms", a, old, body.model_dump()
    )
    await db.flush()
    return SuccessResponse(data=await _admin_view(db, a))


@router.post(
    "/{partner_id}/referrals",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def assign_referral(
    partner_id: UUID,
    body: AssignReferralRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Attribute a store to this partner, replacing any earlier referrer."""
    a = await _load(db, partner_id)
    tenant_id = await db.scalar(
        select(TenantModel.id).where(
            TenantModel.subdomain == body.subdomain.strip().lower()
        )
    )
    if tenant_id is None:
        raise HTTPException(status_code=404, detail="Store not found")
    await assign(db, tenant_id=tenant_id, partner_id=a.id)
    await _audit_change(
        db,
        admin_id,
        "partner_referral_assigned",
        a,
        None,
        {"tenant_id": str(tenant_id)},
    )
    return SuccessResponse(data={"stores": await referred_stores(db, a.id)})


@router.delete(
    "/{partner_id}/referrals/{tenant_id}",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def remove_referral(
    partner_id: UUID,
    tenant_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Detach a store from this partner. Credits already written stay."""
    a = await _load(db, partner_id)
    if not any(r["tenant_id"] == tenant_id for r in await referred_stores(db, a.id)):
        raise HTTPException(status_code=404, detail="Referral not found")
    await assign(db, tenant_id=tenant_id, partner_id=None)
    await _audit_change(
        db, admin_id, "partner_referral_removed", a, {"tenant_id": str(tenant_id)}, None
    )
    return SuccessResponse(data={"stores": await referred_stores(db, a.id)})


@router.put(
    "/{partner_id}/directory",
    response_model=SuccessResponse[AdminPartner],
    dependencies=_STEP_UP,
)
async def put_directory_flags(
    partner_id: UUID,
    body: DirectoryFlagsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Grant or revoke the Verified badge; hide or restore the public profile."""
    a = await _load(db, partner_id)
    old = {"verified": a.verified, "directory_hidden": a.directory_hidden}
    changes = body.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(a, key, value)
    await _audit_change(db, admin_id, "partner_directory_flags", a, old, changes)
    await db.flush()
    return SuccessResponse(data=await _admin_view(db, a))
