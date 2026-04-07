"""External theme management routes (BYOT).

Provides endpoints for merchants to submit, check, and remove external themes:
- POST   /stores/{store_id}/themes/external          — Submit a GitHub repo for building
- GET    /stores/{store_id}/themes/external           — Get current external theme info
- GET    /stores/{store_id}/themes/external/builds/{build_id} — Check build status
- DELETE /stores/{store_id}/themes/external           — Remove external theme
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import (
    get_current_store,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront.public import AVAILABLE_THEMES
from src.api.v1.schemas.tenant.theme import (
    ExternalThemeInfoResponse,
    RemoveExternalThemeRequest,
    StoreThemeListItem,
    StoreThemesListResponse,
    SubmitExternalThemeRequest,
    ThemeBuildResponse,
    ThemeBuildStatus,
    ThemeBuildStatusResponse,
)
from src.core.entities.store import Store
from src.infrastructure.repositories import StoreRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/themes")


# ─── In-memory build status tracking ─────────────────────────────────────────
# In production, this would be stored in Redis or the database.
# Using in-memory dict for the initial implementation.
_build_statuses: dict[str, dict] = {}


@router.get(
    "",
    response_model=SuccessResponse[StoreThemesListResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="List all themes available to a store (built-in + external)",
)
async def list_store_themes(
    store: Store = Depends(get_current_store),
) -> SuccessResponse[StoreThemesListResponse]:
    """List all themes available to this store.

    Includes:
    - All built-in themes (modern, empire, kick-game, etc.)
    - The store's external (BYOT) theme, if any
    """
    theme_settings = store.theme_settings or {}
    external = theme_settings.get("external_theme")
    active_theme_id = theme_settings.get("theme", {}).get("base_theme")

    items: list[StoreThemeListItem] = []

    # Built-in themes
    for theme in AVAILABLE_THEMES:
        items.append(
            StoreThemeListItem(
                id=theme["id"],
                name=theme["name"],
                nameAr=theme["nameAr"],
                layout=theme["layout"],
                description=theme["description"],
                is_external=False,
            )
        )

    # External theme (if any)
    if external and external.get("theme_id"):
        items.append(
            StoreThemeListItem(
                id=external["theme_id"],
                name=external.get("name") or external["theme_id"],
                nameAr=external.get("nameAr") or external["theme_id"],
                layout="external",
                description=external.get("description") or "Custom theme",
                is_external=True,
                bundle_url=external.get("bundle_url"),
                css_url=external.get("css_url"),
                version=external.get("version"),
                source_repo=external.get("source_repo"),
                settings_schema=external.get("settings_schema"),
            )
        )

    return SuccessResponse(
        data=StoreThemesListResponse(
            themes=items,
            active_theme_id=active_theme_id,
        ),
    )


@router.post(
    "/external",
    response_model=SuccessResponse[ThemeBuildResponse],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_store_ownership)],
)
async def submit_external_theme(
    request: SubmitExternalThemeRequest,
    store: Store = Depends(get_current_store),
    store_repo: StoreRepository = Depends(get_store_repository),
) -> SuccessResponse[ThemeBuildResponse]:
    """Submit an external theme from a GitHub repository for building.

    The build process:
    1. Validates the GitHub URL
    2. Queues a background build task
    3. Returns a build_id for status polling

    The build task will:
    - Clone the repo (shallow)
    - Validate the theme contract (theme.json, settings_schema.json, etc.)
    - Run `npm install && numu-theme build`
    - Upload the output to CDN (R2/S3)
    - Update the store's theme_settings with the CDN URLs
    """
    build_id = uuid.uuid4().hex

    # Store build status
    _build_statuses[build_id] = {
        "build_id": build_id,
        "status": ThemeBuildStatus.QUEUED,
        "store_id": str(store.id),
        "github_url": request.github_url,
        "branch": request.branch,
        "theme_id": None,
        "bundle_url": None,
        "css_url": None,
        "error": None,
        "started_at": datetime.now(UTC),
        "completed_at": None,
    }

    # Dispatch Celery task
    try:
        from src.infrastructure.messaging.tasks.theme_build_tasks import (
            build_external_theme,
        )

        build_external_theme.delay(
            store_id=str(store.id),
            github_url=request.github_url,
            branch=request.branch,
            build_id=build_id,
        )
    except Exception as e:
        logger.error("Failed to dispatch theme build task: %s", e)
        _build_statuses[build_id]["status"] = ThemeBuildStatus.FAILED
        _build_statuses[build_id]["error"] = "Failed to queue build task"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Theme build service is temporarily unavailable",
        ) from e

    logger.info(
        "Theme build queued",
        extra={
            "store_id": str(store.id),
            "build_id": build_id,
            "github_url": request.github_url,
        },
    )

    return SuccessResponse(
        data=ThemeBuildResponse(
            build_id=build_id,
            status=ThemeBuildStatus.QUEUED,
            message="Theme build has been queued. Poll the build status endpoint for updates.",
        ),
        message="Theme build queued successfully",
    )


@router.get(
    "/external",
    response_model=SuccessResponse[ExternalThemeInfoResponse],
    dependencies=[Depends(verify_store_ownership)],
)
async def get_external_theme_info(
    store: Store = Depends(get_current_store),
) -> SuccessResponse[ExternalThemeInfoResponse]:
    """Get the current external theme info for a store."""
    theme_settings = store.theme_settings or {}
    external_theme = theme_settings.get("external_theme")

    if not external_theme:
        return SuccessResponse(
            data=ExternalThemeInfoResponse(has_external_theme=False),
        )

    return SuccessResponse(
        data=ExternalThemeInfoResponse(
            has_external_theme=True,
            theme_id=external_theme.get("theme_id"),
            bundle_url=external_theme.get("bundle_url"),
            css_url=external_theme.get("css_url"),
            version=external_theme.get("version"),
            source_repo=external_theme.get("source_repo"),
            built_at=external_theme.get("built_at"),
        ),
    )


@router.get(
    "/external/builds/{build_id}",
    response_model=SuccessResponse[ThemeBuildStatusResponse],
    dependencies=[Depends(verify_store_ownership)],
)
async def get_build_status(
    build_id: str,
    store: Store = Depends(get_current_store),
) -> SuccessResponse[ThemeBuildStatusResponse]:
    """Check the status of a theme build."""
    build_info = _build_statuses.get(build_id)

    if not build_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Build {build_id} not found",
        )

    # Verify this build belongs to the requesting store
    if build_info["store_id"] != str(store.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Build {build_id} not found",
        )

    return SuccessResponse(
        data=ThemeBuildStatusResponse(
            build_id=build_info["build_id"],
            status=build_info["status"],
            theme_id=build_info.get("theme_id"),
            bundle_url=build_info.get("bundle_url"),
            css_url=build_info.get("css_url"),
            error=build_info.get("error"),
            started_at=build_info.get("started_at"),
            completed_at=build_info.get("completed_at"),
        ),
    )


@router.delete(
    "/external",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(verify_store_ownership)],
)
async def remove_external_theme(
    request: RemoveExternalThemeRequest,
    store: Store = Depends(get_current_store),
    store_repo: StoreRepository = Depends(get_store_repository),
) -> SuccessResponse[dict]:
    """Remove the external theme and revert to a built-in theme."""
    theme_settings = dict(store.theme_settings or {})

    if "external_theme" not in theme_settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No external theme configured for this store",
        )

    # Remove external_theme and set base_theme to fallback
    theme_settings.pop("external_theme", None)
    if "theme" not in theme_settings:
        theme_settings["theme"] = {}
    theme_settings["theme"]["base_theme"] = request.fallback_theme

    # Update store theme_settings
    await store_repo.update(store.id, {"theme_settings": theme_settings})

    logger.info(
        "External theme removed",
        extra={
            "store_id": str(store.id),
            "fallback_theme": request.fallback_theme,
        },
    )

    return SuccessResponse(
        data={"removed": True, "fallback_theme": request.fallback_theme},
        message="External theme removed. Store reverted to built-in theme.",
    )
