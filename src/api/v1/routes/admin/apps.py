"""Admin: App review, the App catalog, and the Partner-apps kill switch.

URL: /api/v1/admin/apps. Reading needs ``require_admin``. Review decisions,
app suspension and the kill switch need the 2FA step-up and are written to
``audit_logs``. Catalog curation (listing flags) is audited but needs no 2FA:
it changes what merchants see, not what an app may touch.

See docs/Plans/apps-developer-work/05-SURFACES.md §§ 1.2, 1.3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.app_manifest import Pricing, change_type, price_label
from src.application.services.app_review import (
    OPEN,
    business_days_between,
    due_at,
    go_live,
    live_listing,
    name_change,
    notify_status,
    subject_of,
)
from src.application.services.audit_service import AuditService
from src.application.services.partner_program import (
    KILL_SWITCH_KEY,
    partner_apps_enabled,
)
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppListingModel,
    AppModel,
    AppReviewModel,
    AppVersionModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)
from src.infrastructure.database.models.public.user import UserModel

router = APIRouter(
    prefix="/apps", tags=["Admin - Apps"], dependencies=[Depends(require_admin)]
)
_STEP_UP = [Depends(require_admin_2fa(max_age_seconds=300))]

#: The App Review Guidelines, verbatim (plan 03 § 7). Approve needs all ten.
CHECKLIST = {
    "installs_on_dev_store": "Installs and completes OAuth on a clean development store",
    "scopes_justified": "Every scope is used and justified",
    "arabic_listing": "The Arabic listing is real Egyptian Arabic, not MSA and not a copy",
    "rtl_settings": "The settings form works in RTL, in both languages",
    "clean_uninstall": "Uninstall is clean: token dead, app.uninstalled handled",
    "rejects_bad_signature": "The webhook endpoint rejects a bad X-NUMU-Signature-V1",
    "privacy_policy": "The privacy policy covers the customer data requested",
    "price_matches": "The listing price equals what is charged",
    "not_misleading": "It does not misleadingly duplicate a core feature",
    "support_answers": "Support answers within 2 business days",
}


# ─── Schemas ──────────────────────────────────────────────────────


#: A listing-only round: the checks a listing can be judged on.
LISTING_CHECKS = ("arabic_listing", "not_misleading")


class ReviewRow(BaseModel):
    review_id: UUID
    round: int
    app_id: UUID
    slug: str
    name: dict[str, str]
    icon: str | None
    partner: str | None
    subject: str
    version: str | None
    version_id: UUID | None
    listing_id: UUID | None
    status: str
    change_type: str
    submitted_at: datetime
    age_days: int
    business_days_waiting: int
    due_at: datetime
    overdue: bool
    name_change: bool


class HistoryRound(BaseModel):
    review_id: UUID
    round: int
    subject: str
    version: str | None
    status: str
    submitted_at: datetime
    decided_at: datetime | None
    reviewer: str | None
    checklist: dict | None
    notes: dict | None
    internal_note: str | None


class ReviewDetail(ReviewRow):
    manifest: dict[str, Any] | None
    published_manifest: dict[str, Any] | None
    release_notes: dict | None
    listing: dict[str, Any] | None
    live_listing: dict[str, Any]
    name_change: dict | None  # type: ignore[assignment]
    checklist: dict[str, str]
    required_checks: list[str]
    history: list[HistoryRound]


class ReviewDecision(BaseModel):
    decision: Literal["approve", "request_changes", "reject"]
    checklist: dict[str, bool] = Field(default_factory=dict)
    #: Shown to the partner.
    notes_ar: str | None = Field(default=None, max_length=4000)
    notes_en: str | None = Field(default=None, max_length=4000)
    #: Staff only, never shown to the partner.
    internal_note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _rules(self):
        if self.decision != "approve" and not (self.notes_ar and self.notes_en):
            raise ValueError(
                "request_changes and reject need notes in Arabic and English"
            )
        return self


class CatalogRow(BaseModel):
    id: UUID
    slug: str
    name: str
    first_party: bool
    partner: str | None
    status: str
    version: str
    category: str | None
    listing_flags: dict[str, Any]
    installs_active: int
    installs_total: int
    #: The listing's price block (``plan``, localized label, and for
    #: ``recurring`` ``price_cents`` / ``cycle`` / ``currency``).
    pricing: dict[str, Any] | None = None
    #: A private (custom) app: the one store it installs on.
    private_store_id: UUID | None = None


class ListingFlags(BaseModel):
    catalog_visible: bool | None = None
    featured: bool | None = None
    staff_pick: bool | None = None


class Suspension(BaseModel):
    suspend: bool
    reason: str | None = Field(default=None, max_length=2000)


class KillSwitch(BaseModel):
    enabled: bool


# ─── Helpers ──────────────────────────────────────────────────────


async def _partner_name(db: AsyncSession, user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    return await db.scalar(
        select(PartnerAccountModel.display_name).where(
            PartnerAccountModel.user_id == user_id
        )
    )


async def _published(db: AsyncSession, app_id: UUID) -> dict | None:
    return await db.scalar(
        select(AppVersionModel.manifest).where(
            AppVersionModel.app_id == app_id, AppVersionModel.status == "published"
        )
    )


async def _row(
    db: AsyncSession, review: AppReviewModel, app: AppModel, cls=ReviewRow, **extra
):
    version = (
        await db.get(AppVersionModel, review.version_id) if review.version_id else None
    )
    listing = (
        await db.get(AppListingModel, review.listing_id) if review.listing_id else None
    )
    now = datetime.now(UTC)
    submitted = review.submitted_at.replace(tzinfo=review.submitted_at.tzinfo or UTC)
    renamed = name_change(app, listing.content if listing else None)
    published = await _published(db, app.id)
    if cls is ReviewDetail:
        extra = {
            "manifest": version.manifest if version else None,
            "published_manifest": published,
            "release_notes": version.release_notes if version else None,
            "listing": listing.content if listing else None,
            "live_listing": live_listing(app),
            "checklist": CHECKLIST,
            "required_checks": list(CHECKLIST) if version else list(LISTING_CHECKS),
            **extra,
        }
    return cls(
        review_id=review.id,
        round=review.round,
        app_id=app.id,
        slug=app.slug,
        name=live_listing(app)["name"],
        icon=(version.manifest.get("icon") if version else None) or app.icon_url,
        partner=await _partner_name(db, app.developer_id),
        subject=subject_of(review),
        version=version.version if version else None,
        version_id=review.version_id,
        listing_id=review.listing_id,
        status=review.status,
        change_type=change_type(version.manifest, published)
        if version
        else "listing_only",
        submitted_at=submitted,
        age_days=(now - submitted).days,
        business_days_waiting=business_days_between(submitted, now),
        due_at=due_at(submitted),
        overdue=now > due_at(submitted),
        name_change=renamed if cls is ReviewDetail else renamed is not None,
        **extra,
    )


async def _load_review(db: AsyncSession, review_id: UUID):
    review = await db.get(AppReviewModel, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return review, await db.get(AppModel, review.app_id)


async def _history(db: AsyncSession, app_id: UUID) -> list[HistoryRound]:
    rows = (
        await db.execute(
            select(AppReviewModel, AppVersionModel.version, UserModel.email)
            .outerjoin(AppVersionModel, AppVersionModel.id == AppReviewModel.version_id)
            .outerjoin(UserModel, UserModel.id == AppReviewModel.reviewer_id)
            .where(AppReviewModel.app_id == app_id)
            .order_by(AppReviewModel.round.desc())
        )
    ).all()
    return [
        HistoryRound(
            review_id=r.id,
            round=r.round,
            subject=subject_of(r),
            version=version,
            status=r.status,
            submitted_at=r.submitted_at,
            decided_at=r.decided_at,
            reviewer=email,
            checklist=r.checklist,
            notes=r.notes,
            internal_note=r.internal_note,
        )
        for r, version, email in rows
    ]


async def _subjects(db: AsyncSession, review: AppReviewModel):
    version = (
        await db.get(AppVersionModel, review.version_id) if review.version_id else None
    )
    listing = (
        await db.get(AppListingModel, review.listing_id) if review.listing_id else None
    )
    return version, listing


# ─── Review ───────────────────────────────────────────────────────


@router.get("/review", response_model=SuccessResponse[list[ReviewRow]])
async def review_queue(db: Annotated[AsyncSession, Depends(get_db)]):
    """Open review rounds (versions and listings), oldest first, each with
    its due date (REVIEW_SLA_BUSINESS_DAYS business days after submission)."""
    rows = (
        await db.execute(
            select(AppReviewModel, AppModel)
            .join(AppModel, AppModel.id == AppReviewModel.app_id)
            .where(AppReviewModel.status.in_(OPEN))
            .order_by(AppReviewModel.submitted_at)
        )
    ).all()
    return SuccessResponse(data=[await _row(db, r, app) for r, app in rows])


@router.get("/reviews/{review_id}", response_model=SuccessResponse[ReviewDetail])
async def review_detail(
    review_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Opening a submitted round claims it (in_review) and tells the partner."""
    review, app = await _load_review(db, review_id)
    if review.status == "submitted":
        version, listing = await _subjects(db, review)
        review.status = "in_review"
        review.reviewer_id = admin_id
        for item in (version, listing):
            if item is not None and item.status == "submitted":
                item.status = "in_review"
        if version is not None:
            version.reviewed_by = admin_id
        await db.flush()
        await notify_status(
            db,
            app,
            "in_review",
            version=version.version if version else None,
            listing=listing is not None,
        )
    return SuccessResponse(
        data=await _row(
            db, review, app, ReviewDetail, history=await _history(db, app.id)
        )
    )


@router.post(
    "/reviews/{review_id}/decision",
    response_model=SuccessResponse[ReviewRow],
    dependencies=_STEP_UP,
)
async def decide(
    review_id: UUID,
    body: ReviewDecision,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Approve needs every required check (all ten with a version, the
    listing checks for a listing on its own). A listing approved on its own
    goes live at once; one sent with a version goes live when it publishes."""
    review, app = await _load_review(db, review_id)
    if review.status not in OPEN:
        raise HTTPException(
            status_code=409, detail=f"a {review.status} round is not in review"
        )
    version, listing = await _subjects(db, review)
    required = list(CHECKLIST) if version else list(LISTING_CHECKS)
    missing = [k for k in required if not body.checklist.get(k)]
    if body.decision == "approve" and missing:
        raise HTTPException(
            status_code=422,
            detail=f"approve needs every checklist item: {', '.join(missing)}",
        )
    new = {
        "approve": "approved",
        "request_changes": "changes_requested",
        "reject": "rejected",
    }[body.decision]
    notes = (
        {"ar": body.notes_ar, "en": body.notes_en}
        if body.notes_ar or body.notes_en
        else None
    )
    now = datetime.now(UTC)
    old = review.status
    review.status = new
    review.checklist = body.checklist
    review.notes = notes
    review.internal_note = body.internal_note
    review.reviewer_id = admin_id
    review.decided_at = now
    if version is not None:
        version.status = new
        version.review_checklist = body.checklist
        version.review_notes = notes
        version.reviewed_by = admin_id
        version.reviewed_at = now
    went_live = listing is not None and version is None and new == "approved"
    if went_live:
        await go_live(db, app, listing)
    elif listing is not None:
        listing.status = new
    await AuditService(db).log(
        event_type="admin.app_review",
        action=f"app_review_{new}",
        resource_type="app_review",
        resource_id=str(review.id),
        user_id=admin_id,
        old_value={"status": old},
        new_value={
            "status": new,
            "app": app.slug,
            "version": version.version if version else None,
            "listing": str(listing.id) if listing else None,
        },
    )
    await db.flush()
    await notify_status(
        db,
        app,
        new,
        version=version.version if version else None,
        listing=listing is not None,
        notes=notes,
    )
    if went_live:
        await notify_status(db, app, "published", listing=True)
    return SuccessResponse(data=await _row(db, review, app))


# ─── Catalog ──────────────────────────────────────────────────────


@router.get("", response_model=SuccessResponse[list[CatalogRow]])
async def catalog(db: Annotated[AsyncSession, Depends(get_db)]):
    """Every app, NUMU and Partner together."""
    apps = (
        (await db.execute(select(AppModel).order_by(AppModel.created_at)))
        .scalars()
        .all()
    )
    counts = {
        app_id: (active, total)
        for app_id, active, total in (
            await db.execute(
                select(
                    AppInstallationModel.app_id,
                    func.count().filter(AppInstallationModel.is_enabled),
                    func.count(),
                ).group_by(AppInstallationModel.app_id)
            )
        ).all()
    }
    rows = []
    for a in apps:
        active, total = counts.get(a.id, (0, 0))
        rows.append(
            CatalogRow(
                id=a.id,
                slug=a.slug,
                name=a.name,
                first_party=a.developer_id is None,
                partner=await _partner_name(db, a.developer_id),
                status=getattr(a.status, "value", a.status),
                version=a.version,
                category=a.category,
                listing_flags=a.listing_flags or {},
                installs_active=active,
                installs_total=total,
                pricing=(a.manifest or {}).get("pricing"),
                private_store_id=a.private_store_id,
            )
        )
    return SuccessResponse(data=rows)


@router.patch("/{app_id}/listing-flags", response_model=SuccessResponse[dict])
async def set_listing_flags(
    app_id: UUID,
    body: ListingFlags,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    app = await db.get(AppModel, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    if app.private_store_id and body.catalog_visible:
        raise HTTPException(
            status_code=409, detail="A private app is never listed in the App Store."
        )
    old = dict(app.listing_flags or {})
    app.listing_flags = {**old, **body.model_dump(exclude_none=True)}
    await AuditService(db).log(
        event_type="admin.app_catalog",
        action="app_listing_flags",
        resource_type="app",
        resource_id=str(app.id),
        user_id=admin_id,
        old_value=old,
        new_value=app.listing_flags,
    )
    await db.flush()
    return SuccessResponse(data=app.listing_flags)


@router.post(
    "/{app_id}/suspension", response_model=SuccessResponse[dict], dependencies=_STEP_UP
)
async def suspend_app(
    app_id: UUID,
    body: Suspension,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Suspend: out of the catalog, off every storefront (``is_live`` false).
    Reinstate: back to published if it had a published version, else draft.
    While suspended, every installed token is refused at request time
    (app_tokens.resolve_app_token) and no webhook is delivered to the app
    (webhook_delivery_service.signing_secret); reinstating restores both."""
    app = await db.get(AppModel, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    if body.suspend and not body.reason:
        raise HTTPException(status_code=422, detail="A suspension needs a reason.")
    old = getattr(app.status, "value", app.status)
    if body.suspend:
        app.status = AppStatus.SUSPENDED
    else:
        live = await _published(db, app.id) is not None or app.developer_id is None
        app.status = AppStatus.PUBLISHED if live else AppStatus.DRAFT
    await AuditService(db).log(
        event_type="admin.app_catalog",
        action="app_suspended" if body.suspend else "app_reinstated",
        resource_type="app",
        resource_id=str(app.id),
        user_id=admin_id,
        old_value={"status": old},
        new_value={"status": app.status.value, "reason": body.reason},
    )
    await db.flush()
    if body.suspend:
        await notify_status(db, app, "suspended")
    return SuccessResponse(data={"status": app.status.value})


@router.get("/kill-switch", response_model=SuccessResponse[KillSwitch])
async def get_kill_switch(db: Annotated[AsyncSession, Depends(get_db)]):
    return SuccessResponse(data=KillSwitch(enabled=await partner_apps_enabled(db)))


@router.put(
    "/kill-switch", response_model=SuccessResponse[KillSwitch], dependencies=_STEP_UP
)
async def put_kill_switch(
    body: KillSwitch,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Off: every Partner App leaves the catalog and every storefront at once.
    NUMU Apps are untouched. Phase 4 also rejects every app token."""
    old = await partner_apps_enabled(db)
    await db.execute(
        pg_insert(PlatformConfigModel)
        .values(key=KILL_SWITCH_KEY, value={"enabled": body.enabled})
        .on_conflict_do_update(
            index_elements=[PlatformConfigModel.key],
            set_={"value": {"enabled": body.enabled}},
        )
    )
    await AuditService(db).log(
        event_type="admin.config_change",
        action="partner_apps_on" if body.enabled else "partner_apps_off",
        resource_type="platform_config",
        resource_id=KILL_SWITCH_KEY,
        user_id=admin_id,
        old_value={"enabled": old},
        new_value={"enabled": body.enabled},
    )
    return SuccessResponse(data=body)


@router.put(
    "/{app_id}/pricing", response_model=SuccessResponse[dict], dependencies=_STEP_UP
)
async def set_numu_app_pricing(
    app_id: UUID,
    body: Pricing,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Price a NUMU App (``free`` or ``recurring``). A Partner App's price is
    part of its manifest and changes only through a reviewed version.

    Existing subscribers keep the price they subscribed at; the new price
    applies to new subscriptions and to re-subscribing after a lapse.
    """
    app = await db.get(AppModel, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    if app.developer_id is not None:
        raise HTTPException(
            status_code=409,
            detail="A Partner App's price comes from its reviewed manifest.",
        )
    if body.model == "external":
        raise HTTPException(status_code=422, detail="NUMU Apps are free or recurring.")
    pricing = body.model_dump(mode="json", exclude_none=True)
    label = price_label(pricing)
    new = {
        "plan": body.model,
        "locales": {lang: {"label": label[lang]} for lang in ("ar", "en")},
        **(
            {"price_cents": body.price_cents, "cycle": body.cycle, "currency": "EGP"}
            if body.model == "recurring"
            else {}
        ),
    }
    old = (app.manifest or {}).get("pricing")
    app.manifest = {**(app.manifest or {}), "pricing": new}
    await AuditService(db).log(
        event_type="admin.app_catalog",
        action="numu_app_pricing",
        resource_type="app",
        resource_id=str(app.id),
        user_id=admin_id,
        old_value={"pricing": old},
        new_value={"pricing": new},
    )
    await db.flush()
    return SuccessResponse(data=new)
