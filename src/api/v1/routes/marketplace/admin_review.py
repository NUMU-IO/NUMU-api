"""Admin review routes for marketplace theme moderation.

All routes require SUPER_ADMIN — gated by the same `require_admin`
dependency used by the rest of the admin API. Reads the admin cookie
namespace so impersonation can't evict the admin session.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.repositories import get_marketplace_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    AdminThemeListItem,
    AdminThemeListResponse,
    AdminThemeMetadataPatch,
    AdminThemeMetadataResponse,
    PendingReviewListResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ThemeFlagsPayload,
)
from src.application.services.marketplace_service import MarketplaceService
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

router = APIRouter(
    prefix="/marketplace/admin",
    tags=["Marketplace Admin"],
    dependencies=[Depends(require_admin)],
)


def _svc(
    repo: Annotated[MarketplaceRepository, Depends(get_marketplace_repository)],
) -> MarketplaceService:
    return MarketplaceService(marketplace_repo=repo)


@router.get("/pending", response_model=SuccessResponse[PendingReviewListResponse])
async def list_pending_reviews(
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """List versions awaiting admin review."""
    pending = await svc.list_pending_reviews()
    return SuccessResponse(data=PendingReviewListResponse(pending=pending))


@router.post(
    "/versions/{version_id}/review",
    response_model=SuccessResponse[ReviewDecisionResponse],
    # Step-up gate: approval publishes a theme to every merchant on
    # the platform. A stale admin session shouldn't be able to ship
    # third-party JS storewide. 5-minute freshness window matches the
    # other CRITICAL-tier actions in the codebase.
    dependencies=[Depends(require_admin_2fa(max_age_seconds=300))],
)
async def submit_review(
    version_id: UUID,
    body: ReviewDecisionRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Approve or reject a version. Approval publishes the listing."""
    try:
        data = await svc.review_version(
            reviewer_id=admin_id,
            version_id=version_id,
            decision=body.decision,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=ReviewDecisionResponse(**data))


# ── Phase 1 soft-migration: flag management ──────────────────────────────────
#
# Admin-only endpoints to control which marketplace themes are visible
# (and how broadly) in the public catalog. Default state for a freshly
# published theme is `flags = {}` → INVISIBLE. Admin flips
# catalog_visible / installable / activatable / visible_to_user_ids /
# visible_to_pct here to roll out gradually.
#
# Not gated by 2FA: the operation is reversible (just flip it back),
# and the canary rollout flow requires frequent flag tweaks where 2FA
# friction would push admins to leave themes wide open. The destructive
# action (initial publish) IS still 2FA-gated above.


@router.get(
    "/themes",
    response_model=SuccessResponse[AdminThemeListResponse],
)
async def list_themes_for_admin(
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """List ALL marketplace themes for admin flag management.

    Unlike the public catalog, this surfaces themes regardless of
    ``flags.catalog_visible`` — admins need to see invisible themes to
    flip them visible. Sorts most recent first so newly-submitted
    themes (the ones likely to need admin attention) appear at the top.
    """
    themes = await svc.list_all_themes_admin()
    return SuccessResponse(
        data=AdminThemeListResponse(
            themes=[AdminThemeListItem(**t) for t in themes],
        )
    )


@router.patch(
    "/themes/{theme_id}/flags",
    response_model=SuccessResponse[AdminThemeListItem],
)
async def update_theme_flags(
    theme_id: UUID,
    body: ThemeFlagsPayload,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """Update per-theme feature flags. PATCH semantics — only the
    fields present in the body are touched. Pass ``{}`` to read-only-check.

    Validation: ``visible_to_pct`` must be 0-100. ``visible_to_user_ids``
    entries should be UUIDs but we don't enforce the format here; the
    repo's runtime gate just does string equality so malformed ids are
    inert (no allowlist match).
    """
    if body.visible_to_pct is not None and not (0 <= body.visible_to_pct <= 100):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="visible_to_pct must be between 0 and 100",
        )
    try:
        updated = await svc.update_theme_flags(
            theme_id=theme_id,
            patch=body.model_dump(exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=AdminThemeListItem(**updated))


# ── Session C — file 05 §3.2 — admin theme detail (full metadata) ──────────


@router.get(
    "/themes/{theme_id}",
    response_model=SuccessResponse[AdminThemeMetadataResponse],
)
async def get_theme_detail(
    theme_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """Fetch a single marketplace theme with full admin metadata.

    Distinct from the public catalog's
    ``/marketplace/catalog/themes/{slug}`` (which 404s for non-published
    themes — admins need to edit drafts) and from the admin list
    ``GET /marketplace/admin/themes`` (which only returns the flag-row
    summary, not description/author/screenshots/etc).

    Wraps ``MarketplaceService.update_theme_metadata`` with an empty
    patch so we exercise the same serialiser without writing — see
    `_admin_metadata_dict` in marketplace_service.py.
    """
    try:
        # Empty patch → no-op; service returns the existing row's
        # full admin dict. Cheap (one SELECT + dict serialise).
        data = await svc.update_theme_metadata(theme_id=theme_id, patch={})
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=AdminThemeMetadataResponse(**data))


# ── Session C — file 05 §3.5 — admin versions listing ──────────────────────


@router.get(
    "/themes/{theme_id}/versions",
    response_model=SuccessResponse[list[dict]],
)
async def list_theme_versions(
    theme_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """List every ``marketplace_theme_versions`` row for a theme,
    newest first. Admin-side mirror of the developer's
    ``/marketplace/developer/themes/{theme_id}/versions`` — the
    difference being this endpoint bypasses the developer-ownership
    check (file 04 §3.4 reserved this for admin support cases like
    "user reports a regression, I want to see when the broken bundle
    was published").

    Read-only. The "Pin to default" action (file 05 §3.5) is **not**
    in this session's scope — the UI shows the column with a
    "Coming soon" treatment.
    """
    # `marketplace_repo.list_versions` is the same call the developer
    # path uses internally; the service-level wrap below skips the
    # `theme.developer_id != caller` gate that the dev route enforces.
    versions = await svc._marketplace_repo.list_versions(theme_id)  # type: ignore[attr-defined]
    return SuccessResponse(
        data=[
            {
                "id": str(v.id),
                "version_string": v.version_string,
                "status": v.status.value if hasattr(v.status, "value") else v.status,
                "release_notes": v.release_notes,
                "bundle_url": v.bundle_url,
                "css_url": v.css_url,
                "checksum": v.checksum,
                "size_bytes": v.size_bytes,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
        message=f"{len(versions)} version(s) retrieved",
    )


# ── Session A — file 04 §6.1 — admin price + metadata editor ────────────────


@router.patch(
    "/themes/{theme_id}",
    response_model=SuccessResponse[AdminThemeMetadataResponse],
)
async def update_theme_metadata(
    theme_id: UUID,
    body: AdminThemeMetadataPatch,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """Update an admin-editable theme's price + metadata.

    PATCH semantics — only the fields explicitly present in the body
    are written. Omitted fields preserve their current value. Pass
    ``{}`` to no-op (returns the current row).

    Validation is on the Pydantic side:
      * ``price_cents`` >= 0 (free is ``0``; the field-validator on the
        schema enforces this).
      * ``currency`` is whitelisted to a small set (matches the
        storefront's accepted currencies).
      * ``thumbnail_url`` / ``demo_store_url`` go through the same
        marketplace image-host allowlist as the developer-side
        listing endpoints.

    Returns the new admin-facing serialisation so the merchant-hub UI
    can refresh its row inline.
    """
    patch = body.model_dump(exclude_unset=True)
    try:
        updated = await svc.update_theme_metadata(theme_id=theme_id, patch=patch)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=AdminThemeMetadataResponse(**updated))
