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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.partners import (
    require_agreed_partner,
    require_partner_program,
)
from src.api.responses import SuccessResponse
from src.application.services.app_manifest import (
    SLUG_RE,
    ManifestV1,
    change_type,
    semver_key,
    to_listing_manifest,
)
from src.application.services.app_tokens import mint, store_client_secret
from src.application.services.partner_program import partner_for_user
from src.core.entities.app import AppStatus
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppOAuthClientModel,
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


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


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
):
    """Dry run: the exact rules an upload applies, with nothing stored.
    ``numu app validate`` calls this, so the CLI never drifts from the API."""
    m = _validated(body.manifest)
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
        app.manifest = to_listing_manifest(
            data, developer_name=await _developer_name(db, app)
        )
        app.name = manifest.name.en
        app.description = manifest.tagline.en
        app.icon_url = manifest.icon
        app.category = manifest.category
        app.version = manifest.version
    await db.flush()
    await db.refresh(v)
    return SuccessResponse(
        data=_version_out(v, _published_manifest(versions)), message="Version uploaded"
    )


@router.post(
    "/{app_id}/versions/{version_id}/submit", response_model=SuccessResponse[VersionOut]
)
async def submit_version(
    app_id: UUID,
    version_id: UUID,
    user_id: Annotated[UUID, Depends(require_agreed_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Send a draft (or a version NUMU asked to change) for review."""
    from src.core.url_guard import UnsafeUrlError, assert_webhook_target

    app = await _own_app(db, user_id, app_id)
    v = await _version(db, app, version_id)
    if v.status not in EDITABLE:
        raise _conflict(f"a {v.status} version cannot be submitted")
    # One version in review per app: a reviewer judges a change against the
    # live version, and two queued versions of one app would split that.
    in_review = await db.scalar(
        select(AppVersionModel.version).where(
            AppVersionModel.app_id == app.id,
            AppVersionModel.status.in_(("submitted", "in_review")),
        )
    )
    if in_review:
        raise _conflict(
            f"v{in_review} is already in review; wait for NUMU's decision first"
        )
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
    v.status = "submitted"
    v.submitted_at = datetime.now(UTC)
    await db.flush()
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
    if app.status == AppStatus.PUBLISHED and semver_key(v.version) <= semver_key(
        app.version
    ):
        raise _conflict(f"v{v.version} is not newer than the live v{app.version}")
    await db.execute(
        update(AppVersionModel)
        .where(AppVersionModel.app_id == app.id, AppVersionModel.status == "published")
        .values(status="superseded")
    )
    m = v.manifest
    v.status = "published"
    v.published_at = datetime.now(UTC)
    app.manifest = to_listing_manifest(m, developer_name=await _developer_name(db, app))
    app.name = m["name"]["en"]
    app.description = m["tagline"]["en"]
    app.icon_url = m["icon"]
    app.category = m["category"]
    app.version = v.version
    app.status = AppStatus.PUBLISHED
    await db.flush()
    await db.refresh(app)
    logger.info("partner_app_version_published", app=app.slug, version=v.version)
    return SuccessResponse(
        data=await _app_out(db, app, PartnerAppDetail), message="Published"
    )


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
