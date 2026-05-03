"""Admin theme visibility & lock management.

URL: /api/v1/admin/themes
Requires SUPER_ADMIN role.

Owns the per-theme ``is_visible`` / ``required_plan`` / ``display_order``
flags. The catalog of theme slugs is the static ``AVAILABLE_THEMES`` list
in ``storefront.public`` — this module only stores the admin-controlled
flags and decorates them with the slug's catalog metadata on read.

GET auto-upserts any slug present in the catalog but missing from the
table (idempotent), so new themes added to ``AVAILABLE_THEMES`` show up
without a fresh migration.

PATCH applies updates in a single transaction and returns the full list
so the admin client can ``setQueryData`` without a refetch.
"""

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront.public import AVAILABLE_THEMES
from src.core.interfaces.services.storage_service import StorageBucket
from src.infrastructure.database.models.public.theme_admin_config import (
    ThemeAdminConfigModel,
)

logger = logging.getLogger(__name__)

router = APIRouter()


RequiredPlan = Literal["free", "starter", "pro", "enterprise"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ThemeAdminConfigItem(BaseModel):
    """One row decorated with catalog metadata for the admin UI."""

    theme_slug: str
    name: str
    name_ar: str
    description: str
    is_visible: bool
    required_plan: RequiredPlan
    display_order: int
    # When set, overrides the convention-based preview URL. NULL means the
    # storefront should fall back to {STOREFRONT_ASSETS_BASE_URL}/themes/{slug}/preview.png.
    preview_image_url: str | None = None


class ThemeAdminConfigUpdate(BaseModel):
    """Partial update for one theme. All flag fields optional."""

    theme_slug: str = Field(..., min_length=1, max_length=80)
    is_visible: bool | None = None
    required_plan: RequiredPlan | None = None
    display_order: int | None = Field(None, ge=0, le=10000)


class BatchUpdateRequest(BaseModel):
    themes: list[ThemeAdminConfigUpdate]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _catalog_index() -> dict[str, dict[str, Any]]:
    """Map slug → catalog entry from AVAILABLE_THEMES."""
    return {entry["id"]: entry for entry in AVAILABLE_THEMES}


async def _upsert_missing_slugs(db: AsyncSession) -> None:
    """Ensure every catalog slug has a row. Idempotent — uses ON CONFLICT DO NOTHING."""
    existing = await db.execute(select(ThemeAdminConfigModel.theme_slug))
    existing_slugs = {row[0] for row in existing.all()}

    catalog = _catalog_index()
    missing = [slug for slug in catalog if slug not in existing_slugs]
    if not missing:
        return

    rows = [
        {
            "theme_slug": slug,
            "is_visible": True,
            "required_plan": "free",
            # Append at the end of the current order range; admin can edit later.
            "display_order": 1000 + index,
        }
        for index, slug in enumerate(missing)
    ]
    stmt = (
        pg_insert(ThemeAdminConfigModel)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["theme_slug"])
    )
    await db.execute(stmt)
    await db.commit()
    logger.info("Auto-upserted missing theme_admin_config slugs: %s", missing)


def _decorate(
    row: ThemeAdminConfigModel, catalog: dict[str, dict[str, Any]]
) -> ThemeAdminConfigItem:
    """Merge a config row with its catalog metadata. Unknown slugs fall back to the slug itself."""
    entry = catalog.get(row.theme_slug, {})
    return ThemeAdminConfigItem(
        theme_slug=row.theme_slug,
        name=entry.get("name", row.theme_slug),
        name_ar=entry.get("nameAr", row.theme_slug),
        description=entry.get("description", ""),
        is_visible=row.is_visible,
        required_plan=row.required_plan,  # type: ignore[arg-type]
        display_order=row.display_order,
        preview_image_url=row.preview_image_url,
    )


async def _list_all(db: AsyncSession) -> list[ThemeAdminConfigItem]:
    """Load all rows, decorate, sort by (display_order, name)."""
    catalog = _catalog_index()
    result = await db.execute(
        select(ThemeAdminConfigModel).order_by(
            ThemeAdminConfigModel.display_order.asc(),
            ThemeAdminConfigModel.theme_slug.asc(),
        )
    )
    rows = result.scalars().all()
    return [_decorate(row, catalog) for row in rows]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessResponse[list[ThemeAdminConfigItem]],
    summary="List theme admin config",
    operation_id="admin_list_theme_admin_config",
)
async def list_theme_admin_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[Any, Depends(require_admin)],
):
    """Return all themes with their admin flags, decorated from the catalog."""
    await _upsert_missing_slugs(db)
    items = await _list_all(db)
    return SuccessResponse(data=items, message="Theme admin config retrieved")


@router.patch(
    "",
    response_model=SuccessResponse[list[ThemeAdminConfigItem]],
    summary="Batch-update theme admin config",
    operation_id="admin_update_theme_admin_config",
)
async def update_theme_admin_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[Any, Depends(require_admin)],
    request: BatchUpdateRequest,
):
    """Apply partial updates to one or more theme rows in a single transaction.

    Each update is a partial — only fields explicitly set in the request body
    are applied. Unknown slugs are silently skipped (logged) so the client
    can't accidentally seed garbage rows; admins must add slugs to
    AVAILABLE_THEMES first, then they appear via GET's auto-upsert.
    """
    catalog = _catalog_index()

    for update in request.themes:
        if update.theme_slug not in catalog:
            logger.warning(
                "Skipping theme_admin_config update for unknown slug=%s",
                update.theme_slug,
            )
            continue

        result = await db.execute(
            select(ThemeAdminConfigModel).where(
                ThemeAdminConfigModel.theme_slug == update.theme_slug
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            # Auto-upsert path missed it (e.g. PATCH before GET). Create it now.
            row = ThemeAdminConfigModel(
                theme_slug=update.theme_slug,
                is_visible=True,
                required_plan="free",
                display_order=1000,
            )
            db.add(row)

        patch = update.model_dump(exclude_unset=True, exclude={"theme_slug"})
        for key, value in patch.items():
            if value is not None:
                setattr(row, key, value)

    await db.commit()
    items = await _list_all(db)
    logger.info(
        "Theme admin config updated — themes=%s", [t.theme_slug for t in request.themes]
    )
    return SuccessResponse(data=items, message="Theme admin config saved")


# ---------------------------------------------------------------------------
# Preview screenshot upload
# ---------------------------------------------------------------------------

# Allowed MIME types — same set used for store customization assets, minus
# SVG/ICO since theme previews are full-bleed screenshots.
_ALLOWED_PREVIEW_CONTENT = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
# 8 MB is comfortable for high-res 1280×800+ PNGs and gives admins
# headroom for retina captures without loading the page indefinitely.
_PREVIEW_MAX_BYTES = 8 * 1024 * 1024


@router.post(
    "/{slug}/preview",
    response_model=SuccessResponse[ThemeAdminConfigItem],
    summary="Upload preview screenshot for a theme",
    operation_id="admin_upload_theme_preview",
)
async def upload_theme_preview(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[Any, Depends(require_admin)],
    slug: Annotated[
        str, Path(description="Theme slug (e.g. modern, gilded-glamour-boutique)")
    ],
    file: Annotated[UploadFile, File(description="PNG/JPEG/WebP screenshot")],
):
    """Persist a merchant-facing preview screenshot for ``slug``.

    The previous URL (if any) is left in storage — we don't try to delete
    it because admins commonly upload new captures on top of old ones and
    a stale CDN URL is better than a broken one if the delete fails.
    Storage GC handles long-tail cleanup elsewhere.
    """
    catalog = _catalog_index()
    if slug not in catalog:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown theme slug: {slug}",
        )

    if file.content_type not in _ALLOWED_PREVIEW_CONTENT:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {file.content_type}. Allowed: PNG, JPEG, WebP.",
        )

    content = await file.read()
    if len(content) > _PREVIEW_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {_PREVIEW_MAX_BYTES // (1024 * 1024)}MB limit",
        )
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    ext = (
        file.filename.rsplit(".", 1)[-1].lower()
        if file.filename and "." in file.filename
        else "png"
    )
    # Stable per-theme path; the random suffix forces a fresh CDN URL each
    # upload so admins see their new screenshot without a hard refresh.
    storage_key = f"previews/{slug}/{uuid.uuid4().hex[:10]}.{ext}"

    from src.api.dependencies.services import get_storage_service

    storage = get_storage_service()
    uploaded = await storage.upload_file(
        file_content=content,
        filename=storage_key,
        content_type=file.content_type or "image/png",
        bucket=StorageBucket.THEMES,
    )

    # Ensure a row exists for this slug, then patch the URL.
    result = await db.execute(
        select(ThemeAdminConfigModel).where(ThemeAdminConfigModel.theme_slug == slug)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ThemeAdminConfigModel(
            theme_slug=slug,
            is_visible=True,
            required_plan="free",
            display_order=1000,
        )
        db.add(row)
    row.preview_image_url = uploaded.url

    await db.commit()
    await db.refresh(row)
    logger.info("Uploaded preview screenshot for theme=%s", slug)

    return SuccessResponse(
        data=_decorate(row, catalog),
        message="Preview uploaded",
    )


@router.delete(
    "/{slug}/preview",
    response_model=SuccessResponse[ThemeAdminConfigItem],
    summary="Clear the uploaded preview override for a theme",
    operation_id="admin_clear_theme_preview",
)
async def clear_theme_preview(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[Any, Depends(require_admin)],
    slug: Annotated[str, Path(description="Theme slug")],
):
    """Drop the preview_image_url override.

    The stored asset is left in storage (cheap, GC handles cleanup) — we
    just clear the row's pointer so the storefront falls back to the
    convention URL again.
    """
    catalog = _catalog_index()
    if slug not in catalog:
        raise HTTPException(status_code=404, detail=f"Unknown theme slug: {slug}")

    result = await db.execute(
        select(ThemeAdminConfigModel).where(ThemeAdminConfigModel.theme_slug == slug)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No config row for {slug}")

    row.preview_image_url = None
    await db.commit()
    await db.refresh(row)
    logger.info("Cleared preview override for theme=%s", slug)

    return SuccessResponse(
        data=_decorate(row, catalog),
        message="Preview cleared",
    )
