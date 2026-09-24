"""Partner portal API: apply, profile, development stores.

URL: /api/v1/partners. Every route answers 404 while the Partner program is
closed (``require_partner_program``). A partner is any NUMU user with an
approved ``partner_accounts`` row; owning a store is not required.

See docs/Plans/apps-developer-work/03-PLATFORM-DESIGN.md § 2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.partners import (
    require_agreed_partner,
    require_approved_partner,
    require_partner_program,
)
from src.api.responses import SuccessResponse
from src.application.services.app_billing import partner_statement, statement_csv
from src.application.services.partner_program import (
    AGREEMENT_VERSION,
    MANAGER_ROLES,
    MAX_DEV_STORES,
    partner_for_user,
    partner_membership,
)
from src.core.logging import get_logger
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
    PartnerMemberModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

router = APIRouter(
    prefix="/partners",
    tags=["Partners"],
    dependencies=[Depends(require_partner_program)],
)

DEV_PLAN = "developer"


# ─── Schemas ──────────────────────────────────────────────────────


class PartnerAccountOut(BaseModel):
    id: UUID
    kind: str
    display_name: str
    legal_name: str | None
    country: str
    website_url: str | None
    support_email: str
    support_phone: str | None
    status: str
    agreement_version: str | None
    agreement_accepted_at: datetime | None
    #: Shown to the partner on reject/suspend: {"ar": ..., "en": ...}.
    review_notes: dict | None
    reviewed_at: datetime | None
    created_at: datetime
    share_bps: int | None = None


class PartnerInvitationOut(BaseModel):
    id: UUID
    partner_name: str
    role: str


class PartnerMe(BaseModel):
    account: PartnerAccountOut | None
    #: The caller's role on ``account``: owner, admin or developer.
    role: str | None = None
    #: Pending team invites addressed to the caller's email.
    invitations: list[PartnerInvitationOut] = []
    #: The agreement a new application accepts. An approved partner on an
    #: older version must accept again (``needs_agreement``).
    agreement_version: str
    needs_agreement: bool
    max_dev_stores: int


class _Profile(BaseModel):
    display_name: str = Field(min_length=2, max_length=100)
    legal_name: str | None = Field(default=None, max_length=255)
    country: str = Field(default="EG", min_length=2, max_length=2)
    website_url: str | None = Field(default=None, max_length=2048)
    support_email: EmailStr
    support_phone: str | None = Field(default=None, max_length=40)


class ApplyRequest(_Profile):
    kind: Literal["individual", "company"]
    #: Must be the current AGREEMENT_VERSION, accepted explicitly.
    agreement_version: str
    accept_agreement: bool


#: Profile fields a partner can change but not clear (NOT NULL columns). An
#: explicit null on any other field clears it (e.g. ``website_url``).
_REQUIRED_PROFILE = frozenset({"display_name", "country", "support_email"})


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=100)
    legal_name: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    website_url: str | None = Field(default=None, max_length=2048)
    support_email: EmailStr | None = None
    support_phone: str | None = Field(default=None, max_length=40)
    #: Re-accept a newer agreement.
    accept_agreement_version: str | None = None


class DevStoreOut(BaseModel):
    id: UUID
    name: str
    subdomain: str | None
    url: str | None
    seeded: bool
    created_at: datetime


class CreateDevStoreRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    subdomain: str = Field(min_length=3, max_length=63)


# ─── Helpers ──────────────────────────────────────────────────────


def _out(a: PartnerAccountModel) -> PartnerAccountOut:
    return PartnerAccountOut.model_validate(a, from_attributes=True)


def _client_ip(request: Request) -> str | None:
    forwarded = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    )
    return forwarded or (request.client.host if request.client else None)


def _dev_store(store: StoreModel) -> DevStoreOut:
    return DevStoreOut(
        id=store.id,
        name=store.name,
        subdomain=store.subdomain,
        url=f"https://{store.subdomain}.numueg.app" if store.subdomain else None,
        seeded=bool((store.settings or {}).get("partner_seeded")),
        created_at=store.created_at,
    )


def _dev_stores_query(user_id: UUID):
    return (
        select(StoreModel)
        .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
        .where(StoreModel.owner_id == user_id, TenantModel.plan == DEV_PLAN)
    )


# ─── Account ──────────────────────────────────────────────────────


@router.get(
    "/me", response_model=SuccessResponse[PartnerMe], operation_id="get_partner_me"
)
async def get_me(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    found = await partner_membership(db, user_id)
    account, role = found if found else (None, None)
    email = await db.scalar(select(UserModel.email).where(UserModel.id == user_id))
    invites = (
        (
            await db.execute(
                select(PartnerMemberModel, PartnerAccountModel.display_name)
                .join(
                    PartnerAccountModel,
                    PartnerAccountModel.id == PartnerMemberModel.partner_id,
                )
                .where(
                    func.lower(PartnerMemberModel.email) == (email or "").lower(),
                    PartnerMemberModel.status == "invited",
                )
            )
        ).all()
        if email and found is None
        else []
    )
    return SuccessResponse(
        data=PartnerMe(
            account=_out(account) if account else None,
            role=role,
            invitations=[
                PartnerInvitationOut(id=m.id, partner_name=name, role=m.role)
                for m, name in invites
            ],
            agreement_version=AGREEMENT_VERSION,
            needs_agreement=account is not None
            and account.agreement_version != AGREEMENT_VERSION,
            max_dev_stores=MAX_DEV_STORES,
        )
    )


@router.post(
    "/apply",
    response_model=SuccessResponse[PartnerAccountOut],
    status_code=status.HTTP_201_CREATED,
    operation_id="apply_partner",
)
async def apply(
    body: ApplyRequest,
    request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Apply to the Partner program. A rejected partner may apply again."""
    if not body.accept_agreement or body.agreement_version != AGREEMENT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Accept the current Partner Agreement to apply.",
        )
    verified = await db.scalar(
        select(UserModel.email_verified_at).where(UserModel.id == user_id)
    )
    if verified is None:
        # The API enforces this; the hub's own check is only a redirect.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email before applying.",
        )

    account = await partner_for_user(db, user_id)
    if account is not None and account.status != "rejected":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a partner account ({account.status}).",
        )
    if account is None:
        account = PartnerAccountModel(user_id=user_id)
        db.add(account)

    profile = body.model_dump(exclude={"kind", "agreement_version", "accept_agreement"})
    for key, value in profile.items():
        setattr(account, key, value)
    account.kind = body.kind
    account.country = body.country.upper()
    account.status = "pending"
    account.agreement_version = AGREEMENT_VERSION
    account.agreement_accepted_at = datetime.now(UTC)
    account.agreement_accepted_ip = _client_ip(request)
    account.review_notes = None
    account.reviewed_by = None
    account.reviewed_at = None
    await db.flush()
    await db.refresh(account)
    logger.info("partner_applied", user_id=str(user_id), kind=body.kind)
    return SuccessResponse(data=_out(account), message="Application received")


@router.patch(
    "/me",
    response_model=SuccessResponse[PartnerAccountOut],
    operation_id="update_partner_me",
)
async def update_me(
    body: UpdateProfileRequest,
    request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    found = await partner_membership(db, user_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No partner account")
    account, role = found
    if role not in MANAGER_ROLES or (
        body.accept_agreement_version is not None and role != "owner"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the partner's owner or an admin can do this.",
        )
    changes = body.model_dump(exclude_unset=True, exclude={"accept_agreement_version"})
    for key, value in changes.items():
        if value is None and key in _REQUIRED_PROFILE:
            continue  # a required field can't be cleared, only changed
        setattr(account, key, value.upper() if key == "country" and value else value)
    if body.accept_agreement_version is not None:
        if body.accept_agreement_version != AGREEMENT_VERSION:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="That is not the current Partner Agreement.",
            )
        account.agreement_version = AGREEMENT_VERSION
        account.agreement_accepted_at = datetime.now(UTC)
        account.agreement_accepted_ip = _client_ip(request)
    await db.flush()
    await db.refresh(account)
    return SuccessResponse(data=_out(account), message="Profile updated")


# ─── Development stores ───────────────────────────────────────────


@router.get(
    "/me/dev-stores",
    response_model=SuccessResponse[list[DevStoreOut]],
    operation_id="list_partner_dev_stores",
)
async def list_dev_stores(
    user_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stores = (
        (await db.execute(_dev_stores_query(user_id).order_by(StoreModel.created_at)))
        .scalars()
        .all()
    )
    return SuccessResponse(data=[_dev_store(s) for s in stores])


@router.post(
    "/me/dev-stores",
    response_model=SuccessResponse[DevStoreOut],
    status_code=status.HTTP_201_CREATED,
    operation_id="create_partner_dev_store",
)
async def create_dev_store(
    body: CreateDevStoreRequest,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """A free store on the ``developer`` plan: API access on, never live."""
    from src.api.v1.routes.stores.stores import _seed_default_theme_if_configured
    from src.application.dto.store import CreateStoreDTO
    from src.application.use_cases.stores.create_store import CreateStoreUseCase
    from src.core.exceptions import EntityAlreadyExistsError, ValidationError
    from src.infrastructure.external_services.cloudflare import cloudflare_dns_service
    from src.infrastructure.repositories import OnboardingRepository, StoreRepository
    from src.infrastructure.tenancy.service import TenantService

    count = await db.scalar(
        select(func.count()).select_from(_dev_stores_query(user_id).subquery())
    )
    if (count or 0) >= MAX_DEV_STORES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You can have up to {MAX_DEV_STORES} development stores.",
        )

    use_case = CreateStoreUseCase(
        store_repository=StoreRepository(db),
        tenant_service=TenantService(db),
        onboarding_repository=OnboardingRepository(db),
    )
    try:
        result = await use_case.execute(
            CreateStoreDTO(name=body.name, subdomain=body.subdomain),
            owner_id=user_id,
            plan=DEV_PLAN,
        )
    except EntityAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That subdomain is taken.",
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    # No Search Console ping: a dev store is noindex by design.
    if result.subdomain:
        await cloudflare_dns_service.ensure_store_subdomain(result.subdomain)
    await _seed_default_theme_if_configured(
        db=db, store_id=UUID(str(result.id)), owner_id=user_id
    )
    store = await db.get(StoreModel, UUID(str(result.id)))
    logger.info(
        "partner_dev_store_created", user_id=str(user_id), store_id=str(store.id)
    )
    return SuccessResponse(data=_dev_store(store), message="Development store created")


@router.post(
    "/me/dev-stores/{store_id}/seed",
    response_model=SuccessResponse[DevStoreOut],
    operation_id="seed_partner_dev_store",
)
async def seed_dev_store(
    store_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Arabic products, Egyptian customers, EGP prices, COD orders. Once."""
    from src.application.use_cases.demo.seed_demo_tenant import SeedDemoTenantUseCase

    store = (
        await db.execute(_dev_stores_query(user_id).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="Development store not found")
    if (store.settings or {}).get("partner_seeded"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This store already has sample data.",
        )
    await SeedDemoTenantUseCase(db).execute(store.tenant_id, store.id)
    store.settings = {**(store.settings or {}), "partner_seeded": True}
    await db.flush()
    return SuccessResponse(data=_dev_store(store), message="Sample data added")


# ─── Earnings (paid apps, Phase 7) ────────────────────────────────


@router.get(
    "/me/earnings",
    response_model=SuccessResponse[dict],
    operation_id="get_partner_earnings",
)
async def earnings(
    user_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """What NUMU owes you: your 80% of every paid-app charge, minus payouts
    already sent to your bank account. Payouts are manual bank transfers."""
    from src.application.services.app_billing import (
        app_labels,
        partner_balance,
        partner_payable,
        theme_labels,
    )
    from src.infrastructure.database.models.public.app_billing import (
        PartnerLedgerEntryModel,
    )

    account = await partner_for_user(db, user_id)
    if account is None:  # a super admin without a partner account
        return SuccessResponse(
            data={
                "balance_cents": 0,
                "payable_cents": 0,
                "currency": "EGP",
                "entries": [],
            }
        )
    rows = (
        (
            await db.execute(
                select(PartnerLedgerEntryModel)
                .where(PartnerLedgerEntryModel.partner_id == account.id)
                .order_by(PartnerLedgerEntryModel.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    apps = await app_labels(db, [e.app_id for e in rows])
    apps.update(await theme_labels(db, [e.theme_id for e in rows]))
    return SuccessResponse(
        data={
            "balance_cents": await partner_balance(db, account.id),
            #: Sales become payable 30 days after NUMU collects them.
            "payable_cents": await partner_payable(db, account.id),
            "currency": "EGP",
            "entries": [
                {
                    "kind": e.kind,
                    "amount_cents": e.amount_cents,
                    "gross_cents": e.gross_cents,
                    "platform_fee_cents": e.platform_fee_cents,
                    "share_bps": e.share_bps,
                    "discount_cents": e.discount_cents,
                    "vat_cents": e.vat_cents,
                    "app_id": str(e.app_id) if e.app_id else None,
                    "theme_id": str(e.theme_id) if e.theme_id else None,
                    "app_name": apps.get(e.theme_id or e.app_id, {}).get("name"),
                    "app_slug": apps.get(e.theme_id or e.app_id, {}).get("slug"),
                    "reference": e.reference,
                    "created_at": e.created_at,
                }
                for e in rows
            ],
        }
    )


# ─── Monthly statements (paid apps) ───────────────────────────────


async def _my_statement(db: AsyncSession, user_id: UUID, month: str) -> dict:
    account = await partner_for_user(db, user_id)
    if account is None:
        raise HTTPException(status_code=404, detail="No partner account")
    try:
        return await partner_statement(db, account.id, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get(
    "/me/statements",
    response_model=SuccessResponse[dict],
    operation_id="get_partner_statement",
)
async def statement(
    month: str,
    user_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """One month (``YYYY-MM``, UTC) of your ledger: sales (gross, NUMU's fee,
    your share, at the share each sale was booked with), refunds,
    adjustments, payouts, your coupon discounts, NUMU's VAT on its fee
    (informational), and the opening and closing balance NUMU owes you."""
    return SuccessResponse(data=await _my_statement(db, user_id, month))


@router.get("/me/statements/{month}.csv", operation_id="get_partner_statement_csv")
async def statement_csv_export(
    month: str,
    user_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    body = statement_csv(await _my_statement(db, user_id, month))
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="numu-statement-{month}.csv"'
        },
    )
