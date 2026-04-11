"""Store theme installation management routes (v2).

Mounted at /api/v1/stores/{store_id}/themes/v2 to avoid conflicts with
the existing BYOT themes routes during the migration period.

Endpoints:
  GET    /{store_id}/themes/v2/installed              — list installations
  POST   /{store_id}/themes/v2/install                — install a theme
  GET    /{store_id}/themes/v2/{installation_id}      — get one installation
  POST   /{store_id}/themes/v2/{installation_id}/activate   — activate
  PATCH  /{store_id}/themes/v2/{installation_id}/customize  — save draft
  POST   /{store_id}/themes/v2/{installation_id}/publish    — publish draft
  DELETE /{store_id}/themes/v2/{installation_id}      — uninstall
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.dependencies import (
    get_current_store,
    get_current_user_id,
    get_store_repository,
    verify_store_ownership,
)
from src.api.dependencies.repositories import (
    get_store_theme_repository,
    get_theme_repository,
    get_theme_version_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.theme_v2 import (
    ActivateThemeResponse,
    CustomizeDraftRequest,
    InstallThemeRequest,
    StoreInstalledThemesResponse,
    StoreThemeInstallationResponse,
)
from src.application.services.theme_service import ThemeService
from src.core.entities.store import Store
from src.core.entities.theme import StoreTheme
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.repositories.theme_repository import ThemeRepository
from src.infrastructure.repositories.theme_version_repository import (
    ThemeVersionRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/themes/v2")


# ── Dependency ─────────────────────────────────────────────────────────────────


def _get_svc(
    theme_repo: Annotated[ThemeRepository, Depends(get_theme_repository)],
    version_repo: Annotated[
        ThemeVersionRepository, Depends(get_theme_version_repository)
    ],
    store_theme_repo: Annotated[
        StoreThemeRepository, Depends(get_store_theme_repository)
    ],
) -> ThemeService:
    return ThemeService(
        theme_repo=theme_repo,
        version_repo=version_repo,
        store_theme_repo=store_theme_repo,
    )


# ── Serialization helper ───────────────────────────────────────────────────────


def _serialize(inst: StoreTheme) -> StoreThemeInstallationResponse:
    return StoreThemeInstallationResponse(
        id=str(inst.id),
        store_id=str(inst.store_id),
        theme_id=str(inst.theme_id),
        theme_version_id=str(inst.theme_version_id),
        theme_slug=inst.theme_slug,
        theme_name=inst.theme_name,
        theme_type=inst.theme_type.value if inst.theme_type else None,
        theme_version=inst.theme_version,
        theme_thumbnail_url=inst.theme_thumbnail_url,
        bundle_url=inst.bundle_url,
        css_url=inst.css_url,
        is_active=inst.is_active,
        has_draft_changes=inst.has_draft_changes,
        customization=inst.customization,
        draft_customization=inst.draft_customization if inst.is_active else None,
        installed_at=inst.installed_at.isoformat() if inst.installed_at else None,
        activated_at=inst.activated_at.isoformat() if inst.activated_at else None,
        created_at=inst.created_at.isoformat() if inst.created_at else "",
        updated_at=inst.updated_at.isoformat() if inst.updated_at else "",
    )


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "/installed",
    response_model=SuccessResponse[StoreInstalledThemesResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="List all theme installations for a store",
    tags=["Store Themes V2"],
)
async def list_installations(
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
) -> SuccessResponse[StoreInstalledThemesResponse]:
    """Return all installed themes for this store (active + inactive)."""
    installations = await svc.get_installations(store_id=store.id)
    active_id = None
    serialized = []
    for inst in installations:
        if inst.is_active:
            active_id = str(inst.id)
        serialized.append(_serialize(inst))

    return SuccessResponse(
        data=StoreInstalledThemesResponse(
            installations=serialized,
            active_installation_id=active_id,
        )
    )


@router.post(
    "/install",
    response_model=SuccessResponse[StoreThemeInstallationResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_store_ownership)],
    summary="Install a theme on the store",
    tags=["Store Themes V2"],
)
async def install_theme(
    request: InstallThemeRequest,
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
) -> SuccessResponse[StoreThemeInstallationResponse]:
    """Install a marketplace theme on this store.

    If `version_id` is omitted, the latest version is installed.
    Returns 409 if the theme is already installed.
    """
    tenant_id = store.tenant_id
    if tenant_id is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Store has no tenant_id")

    installation = await svc.install_theme(
        store_id=store.id,
        tenant_id=tenant_id,
        theme_id=UUID(request.theme_id),
        version_id=UUID(request.version_id) if request.version_id else None,
    )
    return SuccessResponse(
        data=_serialize(installation),
        message="Theme installed successfully",
    )


@router.get(
    "/{installation_id}",
    response_model=SuccessResponse[StoreThemeInstallationResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Get a specific installation",
    tags=["Store Themes V2"],
)
async def get_installation(
    installation_id: str,
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
) -> SuccessResponse[StoreThemeInstallationResponse]:
    """Return the detail of a single installation."""
    inst = await svc.get_installation(
        store_id=store.id, installation_id=UUID(installation_id)
    )
    return SuccessResponse(data=_serialize(inst))


@router.post(
    "/{installation_id}/activate",
    response_model=SuccessResponse[ActivateThemeResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Activate an installed theme",
    tags=["Store Themes V2"],
)
async def activate_theme(
    installation_id: str,
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
    store_repo=Depends(get_store_repository),
) -> SuccessResponse[ActivateThemeResponse]:
    """Make the selected installation the active theme for this store.

    Also denormalizes the active theme into `stores.theme_settings` for
    backward compatibility with existing storefront code.
    """
    updated = await svc.activate_theme(
        store_id=store.id,
        installation_id=UUID(installation_id),
        store_repo=store_repo,
    )
    return SuccessResponse(
        data=ActivateThemeResponse(
            activated=True,
            installation=_serialize(updated),
            message=f"Theme '{updated.theme_slug}' is now active",
        ),
        message=f"Theme '{updated.theme_slug}' activated successfully",
    )


@router.patch(
    "/{installation_id}/customize",
    response_model=SuccessResponse[StoreThemeInstallationResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Save draft customization (not yet live)",
    tags=["Store Themes V2"],
)
async def customize_draft(
    installation_id: str,
    request: CustomizeDraftRequest,
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
) -> SuccessResponse[StoreThemeInstallationResponse]:
    """Save draft customization for an installation.

    Changes are saved but NOT published — the live store is unaffected.
    Use the `/publish` endpoint to make them live.
    """
    updated = await svc.save_draft_customization(
        store_id=store.id,
        installation_id=UUID(installation_id),
        draft=request.draft_customization,
    )
    return SuccessResponse(
        data=_serialize(updated),
        message="Draft saved. Use /publish to make changes live.",
    )


@router.post(
    "/{installation_id}/publish",
    response_model=SuccessResponse[StoreThemeInstallationResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Publish draft customization (goes live)",
    tags=["Store Themes V2"],
)
async def publish_customization(
    installation_id: str,
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
    store_repo=Depends(get_store_repository),
) -> SuccessResponse[StoreThemeInstallationResponse]:
    """Promote the draft customization to live.

    Copies `draft_customization` → `customization` and triggers a
    backward-compat write to `stores.theme_settings`.
    Only allowed on the active installation.
    """
    updated = await svc.publish_customization(
        store_id=store.id,
        installation_id=UUID(installation_id),
        store_repo=store_repo,
    )
    return SuccessResponse(
        data=_serialize(updated),
        message="Customization published. Changes are now live.",
    )


@router.post(
    "/{installation_id}/preview",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(verify_store_ownership)],
    summary="Generate a preview URL for an installation",
    tags=["Store Themes V2"],
)
async def generate_preview_url(
    installation_id: str,
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
    current_user_id: UUID = Depends(get_current_user_id),
) -> SuccessResponse[dict]:
    """Generate a short-lived preview URL for the given installation.

    The URL points to the Next.js storefront's `/api/preview?token=xxx`
    endpoint. Clicking it enables Draft Mode so the merchant can preview
    their draft_customization before publishing.

    Token TTL: 30 minutes.
    """
    import os

    from src.api.v1.routes.themes_upload import register_preview_token

    inst = await svc.get_installation(
        store_id=store.id, installation_id=UUID(installation_id)
    )

    # Register token in the in-memory store (replace with Redis in prod)
    token = register_preview_token(
        installation_id=str(inst.id),
        theme_id=str(inst.theme_id),
        version_id=str(inst.theme_version_id),
        store_id=str(store.id),
        user_id=str(current_user_id),
        ttl_seconds=1800,
    )

    # Build the preview URL for this store's subdomain
    storefront_base = os.getenv(
        "NUMU_STOREFRONT_BASE_URL", "https://{subdomain}.numueg.app"
    )
    preview_url = (
        storefront_base.format(subdomain=store.subdomain or store.slug)
        + f"/api/preview?token={token}"
    )

    return SuccessResponse(
        data={
            "preview_url": preview_url,
            "expires_in": 1800,
            "installation_id": installation_id,
        },
        message="Preview URL generated",
    )


@router.delete(
    "/{installation_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(verify_store_ownership)],
    summary="Uninstall a theme from the store",
    tags=["Store Themes V2"],
)
async def uninstall_theme(
    installation_id: str,
    store: Store = Depends(get_current_store),
    svc: ThemeService = Depends(_get_svc),
) -> SuccessResponse[dict]:
    """Remove a theme installation from this store.

    Returns 400 if you try to uninstall the currently active theme.
    Activate another theme first, then uninstall this one.
    """
    await svc.uninstall_theme(
        store_id=store.id,
        installation_id=UUID(installation_id),
    )
    return SuccessResponse(
        data={"uninstalled": True, "installation_id": installation_id},
        message="Theme uninstalled successfully",
    )
