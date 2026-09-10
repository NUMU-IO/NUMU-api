"""Sector preset and capability routes.

URL: /stores/{store_id}/sector-presets and /stores/{store_id}/capabilities

A sector preset seeds the store with the typed fields, categories,
capabilities and home layout that sector normally needs. Capabilities are
what the rest of the platform should branch on — never the sector itself.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_category_repository,
    get_metafield_definition_repository,
    get_store_repository,
    get_store_theme_repository,
)
from src.api.responses import SuccessResponse
from src.application.services.capability_service import (
    CAPABILITIES,
    CapabilityService,
)
from src.application.services.sector_preset_service import SectorPresetService
from src.core.entities.store import Store
from src.core.sector_presets import SECTOR_PRESETS, get_preset
from src.infrastructure.repositories.category_repository import CategoryRepository
from src.infrastructure.repositories.metafield_repository import (
    MetafieldDefinitionRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository

router = APIRouter(prefix="/{store_id}")


class SectorPresetSummary(BaseModel):
    """One sector the merchant can apply."""

    key: str
    name: str
    name_ar: str
    description: str
    namespace: str
    field_count: int
    category_count: int
    capabilities: list[str]
    is_applied: bool


class ApplySectorPresetRequest(BaseModel):
    """Options for applying a preset."""

    apply_categories: bool = True
    apply_theme: bool = Field(
        default=False,
        description=(
            "Write the sector's home layout into the active theme's V3 draft. "
            "Never touches the live storefront — the merchant publishes it."
        ),
    )


class CapabilityResponse(BaseModel):
    """One resolved capability."""

    key: str
    name: str
    name_ar: str
    enabled: bool
    implemented: bool
    min_plan: str


@router.get(
    "/sector-presets",
    response_model=SuccessResponse[list[SectorPresetSummary]],
    summary="List sector presets",
    operation_id="list_sector_presets",
)
async def list_sector_presets(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """List every sector preset, flagging the one this store has applied."""
    applied = (store.settings or {}).get("sector")
    return SuccessResponse(
        data=[
            SectorPresetSummary(
                key=preset.key,
                name=preset.name,
                name_ar=preset.name_ar,
                description=preset.description,
                namespace=preset.namespace,
                field_count=len(preset.fields),
                category_count=len(preset.categories),
                capabilities=preset.capabilities,
                is_applied=preset.key == applied,
            )
            for preset in SECTOR_PRESETS.values()
        ],
        message="Sector presets retrieved successfully",
    )


@router.post(
    "/sector-presets/{preset_key}/apply",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Apply a sector preset",
    operation_id="apply_sector_preset",
)
async def apply_sector_preset(
    preset_key: Annotated[str, Path(description="Sector preset key")],
    request: ApplySectorPresetRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    definition_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    store_theme_repo: Annotated[
        StoreThemeRepository, Depends(get_store_theme_repository)
    ],
):
    """Seed the store from a sector preset. Additive and safe to repeat."""
    preset = get_preset(preset_key)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown sector preset '{preset_key}'",
        )

    service = SectorPresetService(
        definition_repo=definition_repo,
        category_repo=category_repo,
        store_repo=store_repo,
        store_theme_repo=store_theme_repo,
    )
    report = await service.apply(
        store,
        preset,
        apply_categories=request.apply_categories,
        apply_theme=request.apply_theme,
    )
    return SuccessResponse(data=report, message="Sector preset applied successfully")


@router.get(
    "/capabilities",
    response_model=SuccessResponse[list[CapabilityResponse]],
    summary="List resolved capabilities",
    operation_id="list_store_capabilities",
)
async def list_store_capabilities(
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Resolve every capability for this store against its plan and overrides."""
    resolved = await CapabilityService(session).all_for(store)
    return SuccessResponse(
        data=[
            CapabilityResponse(
                key=capability.key,
                name=capability.name,
                name_ar=capability.name_ar,
                enabled=resolved[capability.key],
                implemented=capability.implemented,
                min_plan=capability.min_plan,
            )
            for capability in CAPABILITIES.values()
        ],
        message="Capabilities retrieved successfully",
    )


class SetCapabilityRequest(BaseModel):
    """Per-store capability override."""

    enabled: bool


@router.put(
    "/capabilities/{capability_key}",
    response_model=SuccessResponse[CapabilityResponse],
    summary="Override a capability for this store",
    operation_id="set_store_capability",
)
async def set_store_capability(
    capability_key: Annotated[str, Path(description="Capability key")],
    request: SetCapabilityRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Turn a capability on or off for this store."""
    capability = CAPABILITIES.get(capability_key)
    if capability is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown capability '{capability_key}'",
        )
    if request.enabled and not capability.implemented:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Capability '{capability_key}' is not available yet",
        )

    settings = dict(store.settings or {})
    capabilities = dict(settings.get("capabilities") or {})
    capabilities[capability_key] = request.enabled
    settings["capabilities"] = capabilities
    store.settings = settings
    await store_repo.update(store)

    resolved = await CapabilityService(session).has(store, capability_key)
    return SuccessResponse(
        data=CapabilityResponse(
            key=capability.key,
            name=capability.name,
            name_ar=capability.name_ar,
            enabled=resolved,
            implemented=capability.implemented,
            min_plan=capability.min_plan,
        ),
        message="Capability updated successfully",
    )


def require_capability(capability_key: str):
    """Route dependency that refuses to serve when a capability is off.

    403 rather than 404: the capability exists and is documented, the store
    just does not have it on. Hiding it would only confuse the merchant.
    """

    async def _guard(
        store: Annotated[Store, Depends(verify_store_ownership)],
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> Store:
        if not await CapabilityService(session).has(store, capability_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This store does not have the '{capability_key}' capability enabled",
            )
        return store

    return _guard


__all__ = ["require_capability", "router"]
