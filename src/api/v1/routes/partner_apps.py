"""Partner portal: the partner's apps and their versions.

URL: /api/v1/partners/me/apps. Approved partners only, and only while the
Partner program is open. A version is a validated ``numu.app.json``:

    upload (draft) → submit → [NUMU review] → approved → publish

Publishing copies the listing into ``apps.manifest``, which the catalog, the
hub and the storefront already read. A published Partner App stays out of
the catalog until an admin sets ``listing_flags.catalog_visible``.

See docs/Plans/apps-developer-work/03-PLATFORM-DESIGN.md §§ 4, 7.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.partners import (
    require_agreed_partner,
    require_partner_program,
)
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.api.v1.routes.stores.apps import AppCatalogEntry, _listing
from src.application.services.app_manifest import (
    CATEGORIES,
    SLUG_RE,
    ManifestV1,
    change_type,
    semver_key,
    to_listing_manifest,
)
from src.application.services.app_review import (
    REVIEW_SLA_BUSINESS_DAYS,
    ListingContent,
    due_at,
    editable_listing,
    go_live,
    keep_names,
    listing_manifest,
    live_listing,
    name_change,
    notify_status,
    open_review_of,
    queue_position,
    start_round,
    subject_of,
)
from src.application.services.app_tokens import mint, store_client_secret
from src.application.services.partner_program import partner_for_user
from src.core.entities.app import AppStatus
from src.core.interfaces.services.storage_service import StorageBucket
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppListingModel,
    AppModel,
    AppOAuthClientModel,
    AppReviewModel,
    AppVersionModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

router = APIRouter(
    prefix="/partners/me/apps",
    tags=["Partner Apps"],
    dependencies=[Depends(require_partner_program)],
)


async def _developer_name(db: AsyncSession, app: AppModel) -> str:
    """The listing's developer: the app owner's partner name. A super admin
    may act on an app without a partner account of their own, so this never
    reads the caller's account."""
    owner = await partner_for_user(db, app.developer_id) if app.developer_id else None
    return owner.display_name if owner else "NUMU"


#: A partner edits a version only while it is theirs to edit.
EDITABLE = ("draft", "changes_requested")


# ─── Schemas ──────────────────────────────────────────────────────


class CreateAppRequest(BaseModel):
    slug: str
    name_ar: str = Field(min_length=2, max_length=100)
    name_en: str = Field(min_length=2, max_length=100)


class VersionOut(BaseModel):
    id: UUID
    version: str
    status: str
    change_type: str | None = None
    release_notes: dict | None
    review_notes: dict | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    published_at: datetime | None
    created_at: datetime


class PartnerAppOut(BaseModel):
    id: UUID
    slug: str
    name: str
    name_ar: str | None
    status: str
    version: str
    icon_url: str | None
    category: str | None
    catalog_visible: bool
    client_id: str | None
    installs: int
    latest_version: VersionOut | None


class PartnerAppDetail(PartnerAppOut):
    versions: list[VersionOut]


class CreatedApp(PartnerAppOut):
    #: Shown once. Only its hash is stored.
    client_secret: str


class UploadVersionRequest(BaseModel):
    manifest: dict[str, Any]
    release_notes_ar: str | None = Field(default=None, max_length=2000)
    release_notes_en: str | None = Field(default=None, max_length=2000)


class DevInstallRequest(BaseModel):
    store_id: UUID


class SubmitVersionRequest(BaseModel):
    #: Send the listing draft for review in the same round.
    with_listing: bool = False


class ListingDraftOut(BaseModel):
    id: UUID
    status: str
    content: dict[str, Any]
    version_id: UUID | None
    submitted_at: datetime | None
    updated_at: datetime


class ListingOut(BaseModel):
    listed: bool
    categories: list[str]
    live: dict[str, Any]
    draft: ListingDraftOut | None
    #: The draft (or the live listing) as the merchant App Store shows it.
    preview: AppCatalogEntry


class ReviewRoundOut(BaseModel):
    id: UUID
    round: int
    subject: str
    version: str | None
    version_id: UUID | None
    listing_id: UUID | None
    status: str
    submitted_at: datetime
    decided_at: datetime | None
    notes: dict | None
    checklist: dict | None


class OpenReviewOut(BaseModel):
    review_id: UUID
    position: int
    queue_length: int
    submitted_at: datetime
    expected_by: datetime


class ReviewTimeline(BaseModel):
    rounds: list[ReviewRoundOut]
    open: OpenReviewOut | None
    sla_business_days: int


# ─── Helpers ──────────────────────────────────────────────────────


def _version_out(v: AppVersionModel, published: dict | None = None) -> VersionOut:
    out = VersionOut.model_validate(v, from_attributes=True)
    out.change_type = change_type(v.manifest, published)
    return out


async def _own_app(db: AsyncSession, user_id: UUID, app_id: UUID) -> AppModel:
    app = await db.get(AppModel, app_id)
    if app is None or app.developer_id != user_id:
        raise HTTPException(status_code=404, detail="App not found")
    return app


async def _versions(db: AsyncSession, app_id: UUID) -> list[AppVersionModel]:
    rows = (
        (
            await db.execute(
                select(AppVersionModel).where(AppVersionModel.app_id == app_id)
            )
        )
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda v: semver_key(v.version), reverse=True)


def _published_manifest(versions: list[AppVersionModel]) -> dict | None:
    return next((v.manifest for v in versions if v.status == "published"), None)


async def _app_out(db: AsyncSession, app: AppModel, cls=PartnerAppOut, **extra):
    versions = await _versions(db, app.id)
    published = _published_manifest(versions)
    client_id = await db.scalar(
        select(AppOAuthClientModel.client_id).where(
            AppOAuthClientModel.app_id == app.id
        )
    )
    installs = await db.scalar(
        select(func.count(AppInstallationModel.id)).where(
            AppInstallationModel.app_id == app.id
        )
    )
    name_ar = ((app.manifest or {}).get("app_locales") or {}).get("ar", {}).get("name")
    fields = {
        "id": app.id,
        "slug": app.slug,
        "name": app.name,
        "name_ar": name_ar,
        "status": getattr(app.status, "value", app.status),
        "version": app.version,
        "icon_url": app.icon_url,
        "category": app.category,
        "catalog_visible": bool((app.listing_flags or {}).get("catalog_visible")),
        "client_id": client_id,
        "installs": installs or 0,
        "latest_version": _version_out(versions[0], published) if versions else None,
    }
    if cls is PartnerAppDetail:
        fields["versions"] = [_version_out(v, published) for v in versions]
    return cls(**fields, **extra)


async def _version(
    db: AsyncSession, app: AppModel, version_id: UUID
) -> AppVersionModel:
    v = await db.get(AppVersionModel, version_id)
    if v is None or v.app_id != app.id:
        raise HTTPException(status_code=404, detail="Version not found")
    return v


def _validated(raw: dict[str, Any]) -> ManifestV1:
    """ManifestV1, or a 422 with one line per broken rule. Model-level rules
    (e.g. ``app.uninstalled``) run only once every field is valid."""
    try:
        return ManifestV1.model_validate(raw)
    except ValidationError as exc:
        # One readable line per broken rule: the error envelope carries
        # ``detail`` as its message, and a list would arrive as a repr.
        raise HTTPException(
            status_code=422,
            detail="\n".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'manifest'}: "
                f"{e['msg'].removeprefix('Value error, ')}"
                for e in exc.errors()
            ),
        )


async def _check_pricing(db: AsyncSession, model: str) -> None:
    """Partner Agreement § 11.1: until NUMU billing for Partner Apps is live, a
    Partner App is ``free`` or ``external`` (partner_program.BILLING_KEY)."""
    from src.application.services.partner_program import partner_billing_enabled

    if model == "recurring" and not await partner_billing_enabled(db):
        raise HTTPException(
            status_code=422,
            detail="pricing.model: recurring is not available yet, because NUMU "
            "billing for Partner Apps is not live. Use free or external.",
        )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _apply_manifest(db: AsyncSession, app: AppModel, m: dict) -> None:
    """Copy a version's manifest onto the app row. The app keeps its name
    and, once it has one, its live listing: the manifest never renames."""
    live = await db.scalar(
        select(AppListingModel).where(
            AppListingModel.app_id == app.id, AppListingModel.status == "live"
        )
    )
    built = to_listing_manifest(m, developer_name=await _developer_name(db, app))
    app.manifest = (
        listing_manifest(built, live.content) if live else keep_names(app, built)
    )
    app.description = m["tagline"]["en"]
    app.icon_url = m["icon"]
    app.category = m["category"]
    app.version = m["version"]
    if live:
        app.description = live.content["tagline"]["en"]
        app.category = live.content["category"]


async def _no_open_review(db: AsyncSession, app: AppModel) -> None:
    """One review round per app at a time: a reviewer judges a change
    against the live app, and two queued rounds would split that."""
    if await open_review_of(db, app.id):
        raise _conflict("This app is already in review; wait for NUMU's decision.")


def _draft_out(row: AppListingModel) -> ListingDraftOut:
    return ListingDraftOut.model_validate(row, from_attributes=True)


# ─── Apps ─────────────────────────────────────────────────────────


@router.get("", response_model=SuccessResponse[list[PartnerAppOut]])
async def list_apps(
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    apps = (
        (
            await db.execute(
                select(AppModel)
                .where(AppModel.developer_id == user_id)
                .order_by(AppModel.created_at)
            )
        )
        .scalars()
        .all()
    )
    return SuccessResponse(data=[await _app_out(db, a) for a in apps])


@router.post(
    "",
    response_model=SuccessResponse[CreatedApp],
    status_code=status.HTTP_201_CREATED,
)
async def create_app(
    body: CreateAppRequest,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """A draft app and its OAuth client. The secret is in this response only."""
    if not SLUG_RE.match(body.slug):
        raise HTTPException(
            status_code=422, detail="slug must match ^[a-z][a-z0-9-]{2,40}$"
        )
    if await db.scalar(select(AppModel.id).where(AppModel.slug == body.slug)):
        raise _conflict("That slug is taken.")
    if body.name_ar.strip() == body.name_en.strip():
        raise HTTPException(
            status_code=422, detail="Write the Arabic name in Arabic, not the English."
        )
    app = AppModel(
        slug=body.slug,
        name=body.name_en.strip(),
        developer_id=user_id,
        status=AppStatus.DRAFT,
        version="0.0.0",
        manifest={
            "app_locales": {
                "ar": {"name": body.name_ar.strip()},
                "en": {"name": body.name_en.strip()},
            }
        },
        listing_flags={},
    )
    db.add(app)
    await db.flush()
    secret, _ = mint("numu_cs_")
    client = AppOAuthClientModel(
        app_id=app.id, client_id="numu_ci_" + secrets.token_hex(12)
    )
    await store_client_secret(client, secret)
    db.add(client)
    await db.flush()
    await db.refresh(app)
    logger.info("partner_app_created", user_id=str(user_id), slug=app.slug)
    return SuccessResponse(
        data=await _app_out(db, app, CreatedApp, client_secret=secret),
        message="App created. Save the client secret now: it is not shown again.",
    )


class ValidateRequest(BaseModel):
    manifest: dict[str, Any]


@router.post("/validate", response_model=SuccessResponse[dict])
async def validate_manifest(
    body: ValidateRequest,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Dry run: the exact rules an upload applies, with nothing stored.
    ``numu app validate`` calls this, so the CLI never drifts from the API."""
    m = _validated(body.manifest)
    await _check_pricing(db, m.pricing.model)
    return SuccessResponse(
        data={"valid": True, "slug": m.slug, "version": m.version},
        message="numu.app.json is valid",
    )


@router.get("/{app_id}", response_model=SuccessResponse[PartnerAppDetail])
async def get_app(
    app_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    app = await _own_app(db, user_id, app_id)
    return SuccessResponse(data=await _app_out(db, app, PartnerAppDetail))


@router.post("/{app_id}/client-secret", response_model=SuccessResponse[dict])
async def rotate_secret(
    app_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """A new secret; the old one stops working. Shown once."""
    app = await _own_app(db, user_id, app_id)
    client = (
        await db.execute(
            select(AppOAuthClientModel).where(AppOAuthClientModel.app_id == app.id)
        )
    ).scalar_one()
    # Installed tokens keep working; webhooks and "Open app" links are signed
    # with the new secret from now on (they read it at send time).
    secret, _ = mint("numu_cs_")
    await store_client_secret(client, secret)
    client.secret_rotated_at = datetime.now(UTC)
    logger.info("partner_app_secret_rotated", app=app.slug)
    return SuccessResponse(
        data={"client_id": client.client_id, "client_secret": secret},
        message="Secret rotated. The old secret no longer works.",
    )


# ─── Versions ─────────────────────────────────────────────────────


@router.post(
    "/{app_id}/versions",
    response_model=SuccessResponse[VersionOut],
    status_code=status.HTTP_201_CREATED,
)
async def upload_version(
    app_id: UUID,
    body: UploadVersionRequest,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Validate a ``numu.app.json`` and store it as a draft version."""
    app = await _own_app(db, user_id, app_id)
    manifest = _validated(body.manifest)
    await _check_pricing(db, manifest.pricing.model)
    if manifest.slug != app.slug:
        raise HTTPException(
            status_code=422, detail=f"manifest slug must be {app.slug!r}"
        )
    versions = await _versions(db, app.id)
    if versions and semver_key(manifest.version) <= semver_key(versions[0].version):
        raise _conflict(
            f"version must be higher than {versions[0].version}; bump it in numu.app.json"
        )
    notes = (
        {"ar": body.release_notes_ar, "en": body.release_notes_en}
        if body.release_notes_ar or body.release_notes_en
        else None
    )
    data = manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    v = AppVersionModel(
        app_id=app.id,
        version=manifest.version,
        manifest=data,
        status="draft",
        release_notes=notes,
    )
    db.add(v)
    # A newer upload replaces any older version the partner could still
    # submit, so an outdated version can never be reviewed and published
    # over a newer one.
    for old in versions:
        if old.status in EDITABLE:
            old.status = "superseded"
    if app.status == AppStatus.DRAFT:
        # Never published: the app row mirrors the newest upload, so a
        # dev-install on the partner's own store shows the current listing.
        await _apply_manifest(db, app, data)
    renamed = name_change(app, {"name": data["name"]})
    await db.flush()
    await db.refresh(v)
    return SuccessResponse(
        data=_version_out(v, _published_manifest(versions)),
        message="Version uploaded. The name in numu.app.json is not used: "
        "rename the app in its listing."
        if renamed
        else "Version uploaded",
    )


@router.post(
    "/{app_id}/versions/{version_id}/submit", response_model=SuccessResponse[VersionOut]
)
async def submit_version(
    app_id: UUID,
    version_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: SubmitVersionRequest | None = None,
):
    """Send a draft (or a version NUMU asked to change) for review, with the
    listing draft when ``with_listing``. Each submission is a new round."""
    from src.core.url_guard import UnsafeUrlError, assert_webhook_target

    app = await _own_app(db, user_id, app_id)
    v = await _version(db, app, version_id)
    if v.status not in EDITABLE:
        raise _conflict(f"a {v.status} version cannot be submitted")
    await _no_open_review(db, app)
    listing = None
    if body and body.with_listing:
        listing = await editable_listing(db, app.id)
        if listing is None:
            raise _conflict("There is no listing draft to submit.")
    m = v.manifest
    # The offline check ran at upload; now resolve DNS the way webhook
    # delivery will, so a URL that points inside the network is refused.
    urls = {
        m["app_url"],
        *m["oauth"]["redirect_urls"],
        *(w["url"] for w in m["webhooks"]),
    }
    for url in sorted(urls):
        try:
            assert_webhook_target(url)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=422, detail=f"{url}: {exc}")
    await start_round(db, app, version=v, listing=listing)
    logger.info("partner_app_version_submitted", app=app.slug, version=v.version)
    return SuccessResponse(
        data=_version_out(v, _published_manifest(await _versions(db, app.id))),
        message="Submitted for review",
    )


@router.post(
    "/{app_id}/versions/{version_id}/publish",
    response_model=SuccessResponse[PartnerAppDetail],
)
async def publish_version(
    app_id: UUID,
    version_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Make an approved version the live one. The previous one is superseded."""
    app = await _own_app(db, user_id, app_id)
    if app.status == AppStatus.SUSPENDED:
        raise _conflict("This app is suspended by NUMU.")
    v = await _version(db, app, version_id)
    if v.status != "approved":
        raise _conflict("only an approved version can be published")
    # Approved while billing was live, and it was switched off since.
    await _check_pricing(db, (v.manifest.get("pricing") or {}).get("model", "free"))
    if app.status == AppStatus.PUBLISHED and semver_key(v.version) <= semver_key(
        app.version
    ):
        raise _conflict(f"v{v.version} is not newer than the live v{app.version}")
    await db.execute(
        update(AppVersionModel)
        .where(AppVersionModel.app_id == app.id, AppVersionModel.status == "published")
        .values(status="superseded")
    )
    v.status = "published"
    v.published_at = datetime.now(UTC)
    await _apply_manifest(db, app, v.manifest)
    attached = await db.scalar(
        select(AppListingModel).where(
            AppListingModel.version_id == v.id, AppListingModel.status == "approved"
        )
    )
    if attached:
        await go_live(db, app, attached)
    app.status = AppStatus.PUBLISHED
    await notify_status(db, app, "published", version=v.version)
    await db.flush()
    await db.refresh(app)
    logger.info("partner_app_version_published", app=app.slug, version=v.version)
    return SuccessResponse(
        data=await _app_out(db, app, PartnerAppDetail), message="Published"
    )


# ─── Review timeline ──────────────────────────────────────────────


@router.get("/{app_id}/reviews", response_model=SuccessResponse[ReviewTimeline])
async def review_timeline(
    app_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Every review round of the app, newest first, with NUMU's notes to the
    partner. Staff-only notes are never part of this response."""
    app = await _own_app(db, user_id, app_id)
    rows = (
        await db.execute(
            select(AppReviewModel, AppVersionModel.version)
            .outerjoin(AppVersionModel, AppVersionModel.id == AppReviewModel.version_id)
            .where(AppReviewModel.app_id == app.id)
            .order_by(AppReviewModel.round.desc())
        )
    ).all()
    rounds = [
        ReviewRoundOut(
            id=r.id,
            round=r.round,
            subject=subject_of(r),
            version=version,
            version_id=r.version_id,
            listing_id=r.listing_id,
            status=r.status,
            submitted_at=r.submitted_at,
            decided_at=r.decided_at,
            notes=r.notes,
            checklist=r.checklist,
        )
        for r, version in rows
    ]
    open_row = next(
        (r for r, _ in rows if r.status in ("submitted", "in_review")), None
    )
    pending = None
    if open_row:
        position, length = await queue_position(db, open_row)
        pending = OpenReviewOut(
            review_id=open_row.id,
            position=position,
            queue_length=length,
            submitted_at=open_row.submitted_at,
            expected_by=due_at(open_row.submitted_at),
        )
    return SuccessResponse(
        data=ReviewTimeline(
            rounds=rounds, open=pending, sla_business_days=REVIEW_SLA_BUSINESS_DAYS
        )
    )


# ─── Listing ──────────────────────────────────────────────────────


@router.get("/{app_id}/listing", response_model=SuccessResponse[ListingOut])
async def get_listing(
    app_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """The live listing, the partner's latest listing draft, and a preview
    in the shape the merchant App Store page renders."""
    app = await _own_app(db, user_id, app_id)
    draft = await db.scalar(
        select(AppListingModel)
        .where(
            AppListingModel.app_id == app.id,
            AppListingModel.status.notin_(("live", "superseded")),
        )
        .order_by(AppListingModel.created_at.desc())
        .limit(1)
    )
    live = live_listing(app)
    shown = draft.content if draft else live
    return SuccessResponse(
        data=ListingOut(
            listed=bool((app.listing_flags or {}).get("catalog_visible")),
            categories=list(CATEGORIES),
            live=live,
            draft=_draft_out(draft) if draft else None,
            preview=AppCatalogEntry(
                slug=app.slug,
                name=shown["name"]["en"],
                description=shown["tagline"]["en"],
                icon_url=app.icon_url,
                version=app.version,
                listing=_listing(listing_manifest(app.manifest or {}, shown)),
            ),
        )
    )


@router.put("/{app_id}/listing", response_model=SuccessResponse[ListingDraftOut])
async def save_listing(
    app_id: UUID,
    body: ListingContent,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Save the listing draft. Nothing reaches merchants until it is reviewed."""
    app = await _own_app(db, user_id, app_id)
    content = body.model_dump(mode="json")
    row = await editable_listing(db, app.id)
    if row is None:
        in_review = await db.scalar(
            select(AppListingModel.id).where(
                AppListingModel.app_id == app.id,
                AppListingModel.status.in_(("submitted", "in_review")),
            )
        )
        if in_review:
            raise _conflict("The listing is in review; wait for NUMU's decision.")
        row = AppListingModel(app_id=app.id, status="draft", content=content)
        db.add(row)
    else:
        row.content = content
    await db.flush()
    await db.refresh(row)
    return SuccessResponse(data=_draft_out(row), message="Listing saved")


@router.post(
    "/{app_id}/listing/submit", response_model=SuccessResponse[ListingDraftOut]
)
async def submit_listing(
    app_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Send the listing draft for review on its own. Approved, it goes live."""
    app = await _own_app(db, user_id, app_id)
    row = await editable_listing(db, app.id)
    if row is None:
        raise _conflict("There is no listing draft to submit.")
    await _no_open_review(db, app)
    row.version_id = None
    await start_round(db, app, listing=row)
    await db.refresh(row)
    return SuccessResponse(data=_draft_out(row), message="Submitted for review")


SCREENSHOT_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@router.post("/{app_id}/listing/screenshots", response_model=SuccessResponse[dict])
async def upload_screenshot(
    app_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File()],
):
    """Store one listing screenshot (PNG, JPEG or WebP, 5 MB) and return its
    URL for the listing draft."""
    await _own_app(db, user_id, app_id)
    ext = SCREENSHOT_TYPES.get((file.content_type or "").lower())
    if ext is None:
        raise HTTPException(status_code=400, detail="Use a PNG, JPEG or WebP image.")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="The image is over 5 MB.")
    key = f"apps/{app_id}/screenshots/{secrets.token_hex(8)}.{ext}"
    result = await get_storage_service().upload_file(
        file_content=content,
        filename=key,
        content_type=file.content_type,
        bucket=StorageBucket.STORES,
        key=key,
    )
    return SuccessResponse(data={"url": result.url})


# ─── Dev install ──────────────────────────────────────────────────


@router.post("/{app_id}/dev-install", response_model=SuccessResponse[dict])
async def dev_install(
    app_id: UUID,
    body: DevInstallRequest,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Install the partner's own app, at any status, on one of their own
    development stores. Never on a merchant's store."""
    app = await _own_app(db, user_id, app_id)
    store = (
        await db.execute(
            select(StoreModel)
            .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
            .where(
                StoreModel.id == body.store_id,
                StoreModel.owner_id == user_id,
                TenantModel.plan == "developer",
            )
        )
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="Development store not found")
    await db.execute(
        pg_insert(AppInstallationModel)
        .values(
            tenant_id=store.tenant_id,
            store_id=store.id,
            app_id=app.id,
            is_enabled=True,
            settings={},
        )
        .on_conflict_do_update(
            constraint="uq_app_installation_store_app", set_={"is_enabled": True}
        )
    )
    return SuccessResponse(
        data={"store_id": str(store.id), "slug": app.slug},
        message="Installed on your dev store",
    )
