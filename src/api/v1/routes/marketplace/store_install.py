"""Store installation routes for marketplace themes.

All endpoints are scoped to a `store_id` path param and gated by
`verify_store_ownership` so a merchant can only manage installs for
stores they own.

Mounted at `/stores/{store_id}/marketplace/...` from routes/__init__.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_current_user_id,
    get_store_repository,
    verify_store_ownership,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_marketplace_repository,
    get_store_theme_repository,
    get_theme_repository,
    get_theme_version_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    ActivateThemeRequest,
    InstallationResponse,
    InstalledListResponse,
    InstallThemeRequest,
)
from src.application.services.marketplace_service import MarketplaceService
from src.infrastructure.repositories import (
    MarketplaceRepository,
    StoreRepository,
    StoreThemeRepository,
)
from src.infrastructure.repositories.theme_repository import ThemeRepository
from src.infrastructure.repositories.theme_version_repository import (
    ThemeVersionRepository,
)

router = APIRouter(
    prefix="/marketplace",
    tags=["Marketplace Store Install"],
    dependencies=[Depends(verify_store_ownership)],
)


def _svc(
    marketplace_repo: Annotated[
        MarketplaceRepository, Depends(get_marketplace_repository)
    ],
    store_theme_repo: Annotated[
        StoreThemeRepository, Depends(get_store_theme_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    theme_repo: Annotated[ThemeRepository, Depends(get_theme_repository)],
    version_repo: Annotated[
        ThemeVersionRepository, Depends(get_theme_version_repository)
    ],
) -> MarketplaceService:
    # Phase 3a — activate_theme needs theme_repo + version_repo to bridge
    # marketplace_themes (catalog) → themes (runtime entity) so
    # store_themes.theme_id has somewhere valid to point. Install /
    # uninstall / list don't strictly need these but it's cleaner to
    # build the service consistently for the whole route file.
    return MarketplaceService(
        marketplace_repo=marketplace_repo,
        store_theme_repo=store_theme_repo,
        store_repo=store_repo,
        theme_repo=theme_repo,
        version_repo=version_repo,
    )


@router.get("/installed", response_model=SuccessResponse[InstalledListResponse])
async def list_installed(
    store_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """List marketplace themes installed on a store."""
    items = await svc.list_installed(store_id)
    return SuccessResponse(data=InstalledListResponse(installed=items))


@router.post(
    "/install",
    response_model=SuccessResponse[InstallationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def install_theme(
    store_id: UUID,
    body: InstallThemeRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Install (or reinstall) the latest published version of a theme.

    Installation does NOT activate the theme — call `/activate` after
    installing. This makes upgrades reversible.

    Paid themes require a successful purchase by `user_id` for the same
    `marketplace_theme_id` (enforced in MarketplaceService.install_theme).
    """
    try:
        data = await svc.install_theme(
            store_id=store_id,
            marketplace_theme_id=UUID(body.marketplace_theme_id),
            user_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=InstallationResponse(**data))


@router.post(
    "/activate",
    response_model=SuccessResponse[InstallationResponse],
)
async def activate_theme(
    store_id: UUID,
    body: ActivateThemeRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Activate an installed marketplace theme.

    Side effects: marks this install active (deactivating any other
    marketplace install for the store), seeds the V3 draft from the
    version's presets, and triggers Next.js cache invalidation.
    """
    try:
        data = await svc.activate_theme(
            store_id=store_id,
            marketplace_theme_id=UUID(body.marketplace_theme_id),
            user_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=InstallationResponse(**data))


@router.delete("/uninstall/{theme_id}")
async def uninstall_theme(
    store_id: UUID,
    theme_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """Uninstall a marketplace theme from a store.

    Soft-uninstalls — the underlying customization is preserved so a
    later reinstall restores the merchant's prior settings.
    """
    ok = await svc.uninstall_theme(store_id, theme_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="theme is not installed on this store",
        )
    return SuccessResponse(
        data={"uninstalled": True, "marketplace_theme_id": str(theme_id)},
        message="Theme uninstalled",
    )


@router.get("/upgrades")
async def list_upgrades(
    store_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """List installed marketplace themes that have a newer published
    version available. Hub renders an "Upgrade available" badge per
    item using `installed_version_string` → `latest_version_string`.
    """
    upgrades = await svc.list_upgradeable(store_id)
    return SuccessResponse(
        data={"upgrades": upgrades},
        message=f"{len(upgrades)} theme(s) have updates available"
        if upgrades
        else "All installed themes are up to date",
    )


@router.post("/upgrade/{theme_id}")
async def upgrade_theme(
    store_id: UUID,
    theme_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Pin an installed marketplace theme to its latest published
    version + reactivate. Customization is preserved (the activate path
    prefers existing customization_v3 over fresh presets).
    """
    try:
        data = await svc.upgrade_theme(
            store_id=store_id,
            marketplace_theme_id=theme_id,
            user_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=data)


# ── Merchant snapshot browser (Session F, file 06 §9) ───────────────────────
#
# Read-only, merchant-scoped mirror of the admin snapshot list
# (admin/stores.py). The router-level `verify_store_ownership` dependency
# guarantees the caller owns `store_id`, and Postgres RLS means the
# snapshot query can only ever see rows for that store — a merchant can
# never read another store's snapshots even if they tampered with the
# path param. Restore stays unbuilt (disabled-with-tooltip in the UI),
# same posture as the admin browser in Session C.


class MerchantSnapshotItem(BaseModel):
    """One row in the merchant's own snapshot browser."""

    id: str
    store_id: str
    theme_id: str | None = None
    theme_version_id: str | None = None
    reason: str
    created_at: str
    restored_at: str | None = None
    # Cheap "N sections customized" signals derived server-side so the UI
    # doesn't download the full (50-100 KB) customization_v3 per row.
    section_count: int = 0
    section_group_count: int = 0
    # Resolved from-theme name for the "transition" hint; NULL when the
    # snapshot's theme_id was SET NULL by a downstream theme delete.
    theme_name: str | None = None


class MerchantSnapshotListResponse(BaseModel):
    snapshots: list[MerchantSnapshotItem]


@router.get(
    "/snapshots",
    response_model=SuccessResponse[MerchantSnapshotListResponse],
    summary="List the calling merchant's own theme snapshots",
    operation_id="merchant_list_store_theme_snapshots",
)
async def list_store_snapshots(
    store_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
) -> SuccessResponse[MerchantSnapshotListResponse]:
    """List this store's theme snapshots, newest first (read-only).

    Append-only audit trail of every theme switch/customize. Restore is
    NOT exposed (the rollback endpoint is deferred pending explicit
    authorization); the merchant UI renders Restore disabled-with-tooltip.

    Auth: `verify_store_ownership` (router-level) + RLS tenant scoping —
    the merchant can only ever read their own store's rows.
    """
    from src.infrastructure.database.models.tenant.theme import (
        StoreThemeSnapshotModel,  # noqa: F401  (imported for parity/clarity)
        ThemeModel,
    )
    from src.infrastructure.repositories.store_theme_snapshot_repository import (
        StoreThemeSnapshotRepository,
    )

    snapshot_repo = StoreThemeSnapshotRepository(db)
    rows = await snapshot_repo.list_for_store(store_id=store_id, limit=limit)

    # Bulk-resolve from-theme names so the UI renders the transition hint
    # without N round-trips.
    theme_ids = {r.theme_id for r in rows if r.theme_id is not None}
    theme_name_by_id: dict[UUID, str] = {}
    if theme_ids:
        name_result = await db.execute(
            select(ThemeModel.id, ThemeModel.name).where(ThemeModel.id.in_(theme_ids))
        )
        theme_name_by_id = dict(name_result.all())

    items: list[MerchantSnapshotItem] = []
    for row in rows:
        cust_v3 = row.customization_v3 or {}
        templates = cust_v3.get("templates", {}) if isinstance(cust_v3, dict) else {}
        section_count = 0
        if isinstance(templates, dict):
            for tpl in templates.values():
                if isinstance(tpl, dict):
                    sections = tpl.get("sections", {})
                    if isinstance(sections, dict | list):
                        section_count += len(sections)
        section_groups = (
            cust_v3.get("section_groups", {}) if isinstance(cust_v3, dict) else {}
        )
        section_group_count = (
            len(section_groups) if isinstance(section_groups, dict) else 0
        )

        items.append(
            MerchantSnapshotItem(
                id=str(row.id),
                store_id=str(row.store_id),
                theme_id=str(row.theme_id) if row.theme_id else None,
                theme_version_id=(
                    str(row.theme_version_id) if row.theme_version_id else None
                ),
                reason=row.reason,
                created_at=row.created_at.isoformat() if row.created_at else "",
                restored_at=(row.restored_at.isoformat() if row.restored_at else None),
                section_count=section_count,
                section_group_count=section_group_count,
                theme_name=(
                    theme_name_by_id.get(row.theme_id) if row.theme_id else None
                ),
            )
        )

    return SuccessResponse(data=MerchantSnapshotListResponse(snapshots=items))


@router.post(
    "/developer-install/{theme_id}",
    status_code=status.HTTP_201_CREATED,
)
async def developer_install_theme(
    store_id: UUID,
    theme_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Install a developer's own theme on this store, bypassing the
    "must be published" gate.

    Authorization layers (defense-in-depth):
      1. `verify_store_ownership` (router-level) — caller owns the store.
      2. `theme.developer_id == user_id` (service-level) — caller owns
         the theme listing.

    Use case: developer who built a theme and wants to use it on their
    own store immediately, without waiting for marketplace review.
    Falls back to the latest version with a bundle_url, regardless of
    review status. Activate via the regular `/activate` endpoint after
    install — the developer install path doesn't auto-activate so the
    merchant has the same control they'd have over a marketplace theme.
    """
    try:
        data = await svc.developer_install_theme(
            developer_id=user_id,
            store_id=store_id,
            marketplace_theme_id=theme_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=data)
