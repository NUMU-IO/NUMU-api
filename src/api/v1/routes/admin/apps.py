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
from src.application.services.audit_service import AuditService
from src.application.services.partner_program import (
    KILL_SWITCH_KEY,
    partner_apps_enabled,
)
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppVersionModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

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


class ReviewRow(BaseModel):
    version_id: UUID
    app_id: UUID
    slug: str
    name: dict[str, str]
    icon: str | None
    partner: str | None
    version: str
    status: str
    change_type: str
    submitted_at: datetime | None
    age_days: int


class VersionDetail(ReviewRow):
    manifest: dict[str, Any]
    published_manifest: dict[str, Any] | None
    release_notes: dict | None
    review_notes: dict | None
    review_checklist: dict | None
    checklist: dict[str, str]


class ReviewDecision(BaseModel):
    decision: Literal["approve", "request_changes", "reject"]
    checklist: dict[str, bool] = Field(default_factory=dict)
    notes_ar: str | None = Field(default=None, max_length=4000)
    notes_en: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _rules(self):
        if self.decision == "approve":
            missing = [k for k in CHECKLIST if not self.checklist.get(k)]
            if missing:
                raise ValueError(
                    f"approve needs every checklist item: {', '.join(missing)}"
                )
        elif not (self.notes_ar and self.notes_en):
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
    db: AsyncSession, v: AppVersionModel, app: AppModel, cls=ReviewRow, **extra
):
    published = await _published(db, app.id)
    since = v.submitted_at or v.created_at
    return cls(
        version_id=v.id,
        app_id=app.id,
        slug=app.slug,
        name=v.manifest.get("name") or {"en": app.name},
        icon=v.manifest.get("icon"),
        partner=await _partner_name(db, app.developer_id),
        version=v.version,
        status=v.status,
        change_type=change_type(v.manifest, published),
        submitted_at=v.submitted_at,
        age_days=(datetime.now(UTC) - since).days,
        **({"published_manifest": published} if cls is VersionDetail else {}),
        **extra,
    )


async def _load_version(db: AsyncSession, version_id: UUID):
    v = await db.get(AppVersionModel, version_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return v, await db.get(AppModel, v.app_id)


# ─── Review ───────────────────────────────────────────────────────


@router.get("/review", response_model=SuccessResponse[list[ReviewRow]])
async def review_queue(db: Annotated[AsyncSession, Depends(get_db)]):
    """Submitted and in-review versions, oldest first."""
    rows = (
        await db.execute(
            select(AppVersionModel, AppModel)
            .join(AppModel, AppModel.id == AppVersionModel.app_id)
            .where(AppVersionModel.status.in_(("submitted", "in_review")))
            .order_by(AppVersionModel.submitted_at)
        )
    ).all()
    return SuccessResponse(data=[await _row(db, v, app) for v, app in rows])


@router.get("/versions/{version_id}", response_model=SuccessResponse[VersionDetail])
async def version_detail(
    version_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Opening a submitted version claims it (in_review)."""
    v, app = await _load_version(db, version_id)
    if v.status == "submitted":
        v.status = "in_review"
        v.reviewed_by = admin_id
        await db.flush()
    return SuccessResponse(
        data=await _row(
            db,
            v,
            app,
            VersionDetail,
            manifest=v.manifest,
            release_notes=v.release_notes,
            review_notes=v.review_notes,
            review_checklist=v.review_checklist,
            checklist=CHECKLIST,
        )
    )


@router.post(
    "/versions/{version_id}/review",
    response_model=SuccessResponse[ReviewRow],
    dependencies=_STEP_UP,
)
async def review(
    version_id: UUID,
    body: ReviewDecision,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    v, app = await _load_version(db, version_id)
    if v.status not in ("submitted", "in_review"):
        raise HTTPException(
            status_code=409, detail=f"a {v.status} version is not in review"
        )
    old = v.status
    v.status = {
        "approve": "approved",
        "request_changes": "changes_requested",
        "reject": "rejected",
    }[body.decision]
    v.review_checklist = body.checklist
    v.review_notes = (
        {"ar": body.notes_ar, "en": body.notes_en}
        if body.notes_ar or body.notes_en
        else None
    )
    v.reviewed_by = admin_id
    v.reviewed_at = datetime.now(UTC)
    await AuditService(db).log(
        event_type="admin.app_review",
        action=f"app_version_{v.status}",
        resource_type="app_version",
        resource_id=str(v.id),
        user_id=admin_id,
        old_value={"status": old},
        new_value={"status": v.status, "app": app.slug, "version": v.version},
    )
    await db.flush()
    return SuccessResponse(data=await _row(db, v, app))


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
