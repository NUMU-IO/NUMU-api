"""External theme management routes (BYOT).

Provides endpoints for merchants to submit, check, and remove external themes:
- POST   /stores/{store_id}/themes/external          — Submit a GitHub repo for building
- POST   /stores/{store_id}/themes/external/dev-mode — Connect a local dev server URL
- GET    /stores/{store_id}/themes/external           — Get current external theme info
- GET    /stores/{store_id}/themes/external/builds/{build_id} — Check build status
- DELETE /stores/{store_id}/themes/external           — Remove external theme
"""

import logging
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import (
    get_current_store,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront.public import AVAILABLE_THEMES
from src.api.v1.schemas.tenant.theme import (
    ConnectDevServerRequest,
    ExternalThemeInfoResponse,
    RebuildExternalThemeRequest,
    RemoveExternalThemeRequest,
    StoreThemeListItem,
    StoreThemesListResponse,
    SubmitExternalThemeRequest,
    ThemeBuildResponse,
    ThemeBuildStatus,
    ThemeBuildStatusResponse,
    ThemeValidationResponse,
    ValidationErrorModel,
)
from src.core.entities.store import Store
from src.infrastructure.cache.theme_build_store import get_theme_build_store
from src.infrastructure.repositories import StoreRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/themes")


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
                section_schemas=external.get("section_schemas"),
                mode=external.get("mode"),
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
    await get_theme_build_store().set(
        build_id,
        {
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
        },
    )

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
        await get_theme_build_store().update(
            build_id,
            {"status": ThemeBuildStatus.FAILED, "error": "Failed to queue build task"},
        )
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


# ─── Dev mode: connect a local theme server ──────────────────────────────────


@router.post(
    "/external/dev-mode",
    response_model=SuccessResponse[ExternalThemeInfoResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Connect a local theme dev server (numu-theme dev)",
)
async def connect_dev_server(
    request: ConnectDevServerRequest,
    store: Store = Depends(get_current_store),
    store_repo: StoreRepository = Depends(get_store_repository),
) -> SuccessResponse[ExternalThemeInfoResponse]:
    """Connect a local theme dev server URL to this store.

    The dev server should be running `numu-theme dev` (default port: 4321).
    The backend probes the dev server to verify it's reachable, then stores
    the URL in theme_settings.external_theme with mode="dev" so the storefront
    knows to bypass caching and always re-fetch the bundle.

    Used for the local development workflow:
    1. Developer runs `numu-theme dev` in their theme repo
    2. Developer pastes the URL (http://localhost:4321) into the dashboard
    3. Storefront loads from the dev URL, no caching
    4. Developer edits files → vite rebuilds → developer refreshes storefront
    """
    dev_url = request.dev_url.rstrip("/")
    bundle_url = f"{dev_url}/theme.js"
    css_url = f"{dev_url}/theme.css"
    manifest_url = f"{dev_url}/theme.json"
    schema_url = f"{dev_url}/settings_schema.json"
    sections_url = f"{dev_url}/sections.json"

    # Probe the dev server: fetch theme.json to verify it's a valid theme
    manifest: dict = {}
    settings_schema: dict | None = None
    sections_manifest: dict | None = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # theme.json is the source manifest — most dev servers serve it
            try:
                manifest_res = await client.get(manifest_url)
                if manifest_res.status_code == 200:
                    manifest = manifest_res.json()
            except Exception as e:
                logger.warning(
                    "Could not fetch theme.json from %s: %s", manifest_url, e
                )

            # Try to fetch settings_schema.json
            try:
                schema_res = await client.get(schema_url)
                if schema_res.status_code == 200:
                    settings_schema = schema_res.json()
            except Exception as e:
                logger.warning(
                    "Could not fetch settings_schema.json from %s: %s", schema_url, e
                )

            # Try to fetch sections.json (optional — only present if the
            # bundle ships custom sections). Used by the dashboard's section
            # picker so merchants can drop external sections into templates.
            try:
                sections_res = await client.get(sections_url)
                if sections_res.status_code == 200:
                    sections_manifest = sections_res.json()
            except Exception as e:
                logger.warning(
                    "Could not fetch sections.json from %s: %s", sections_url, e
                )

            # Verify theme.js is reachable
            bundle_res = await client.head(bundle_url)
            if bundle_res.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Dev server at {dev_url} is not serving theme.js "
                        f"(got HTTP {bundle_res.status_code}). "
                        "Make sure `numu-theme dev` is running."
                    ),
                )
    except HTTPException:
        raise
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Could not reach dev server at {dev_url}. "
                f"Make sure `numu-theme dev` is running and the URL is correct. "
                f"Error: {e}"
            ),
        ) from e

    if not manifest.get("id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dev server at {dev_url} did not return a valid theme.json. "
                "Make sure your theme has theme.json with an 'id' field."
            ),
        )

    theme_id = manifest["id"]

    # Preserve any merchant_settings that were set against this same theme
    # before the reconnect, so a dev-server restart doesn't wipe customizations.
    existing_external = (store.theme_settings or {}).get("external_theme") or {}
    preserved_merchant_settings = (
        existing_external.get("merchant_settings")
        if existing_external.get("theme_id") == theme_id
        else None
    )

    # Update store theme_settings with the dev server URLs
    theme_settings = dict(store.theme_settings or {})
    theme_settings["external_theme"] = {
        "bundle_url": bundle_url,
        "css_url": css_url,
        "theme_id": theme_id,
        "name": manifest.get("name", theme_id),
        "nameAr": manifest.get("nameAr", theme_id),
        "description": manifest.get("description", "Local dev theme"),
        "version": manifest.get("version", "0.0.0-dev"),
        "author": manifest.get("author", "Developer"),
        "tags": manifest.get("tags", []),
        "source_repo": dev_url,  # Used as the "source" for the dashboard UI
        "built_at": datetime.now(UTC).isoformat(),
        "settings_schema": settings_schema,
        # Section schemas extracted from the bundle's sections.json — fed to
        # the dashboard's section picker so merchants can compose templates
        # with the bundle's custom sections.
        "section_schemas": sections_manifest,
        "mode": "dev",  # Storefront uses this to bypass caching
    }
    if preserved_merchant_settings is not None:
        theme_settings["external_theme"]["merchant_settings"] = preserved_merchant_settings

    # Set base_theme to the external theme's ID so it becomes active immediately
    if "theme" not in theme_settings:
        theme_settings["theme"] = {}
    theme_settings["theme"]["base_theme"] = theme_id

    await store_repo.update(store.id, {"theme_settings": theme_settings})

    logger.info(
        "Dev server connected",
        extra={
            "store_id": str(store.id),
            "theme_id": theme_id,
            "dev_url": dev_url,
        },
    )

    return SuccessResponse(
        data=ExternalThemeInfoResponse(
            has_external_theme=True,
            theme_id=theme_id,
            bundle_url=bundle_url,
            css_url=css_url,
            version=manifest.get("version"),
            source_repo=dev_url,
            built_at=datetime.now(UTC),
        ),
        message=f"Dev server connected. Theme '{theme_id}' is now active.",
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
    build_info = await get_theme_build_store().get(build_id)

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


@router.post(
    "/external/rebuild",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessResponse[ThemeBuildResponse],
    dependencies=[Depends(verify_store_ownership)],
)
async def rebuild_external_theme(
    request: RebuildExternalThemeRequest,
    store: Store = Depends(get_current_store),
) -> SuccessResponse[ThemeBuildResponse]:
    """Rebuild the current external theme using the stored source_repo URL."""
    theme_settings = store.theme_settings or {}
    external = theme_settings.get("external_theme")
    if not external or not external.get("source_repo"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store does not have a GitHub-connected external theme",
        )
    if external.get("mode") == "dev":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot rebuild a dev-mode theme",
        )

    build_id = uuid.uuid4().hex
    github_url = external["source_repo"]
    branch = request.branch

    await get_theme_build_store().set(
        build_id,
        {
            "build_id": build_id,
            "status": ThemeBuildStatus.QUEUED,
            "message": "Queued for rebuild",
            "store_id": str(store.id),
            "github_url": github_url,
            "branch": branch,
            "started_at": datetime.now(UTC).isoformat(),
        },
    )

    try:
        from src.infrastructure.messaging.tasks.theme_build_tasks import (
            build_external_theme,
        )

        build_external_theme.delay(
            store_id=str(store.id),
            github_url=github_url,
            branch=branch,
            build_id=build_id,
        )
    except Exception as e:
        logger.error(f"Failed to queue theme rebuild task: {e}")
        await get_theme_build_store().update(
            build_id,
            {
                "status": ThemeBuildStatus.FAILED,
                "message": "Failed to start build task",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue rebuild task",
        )

    return SuccessResponse(
        data=ThemeBuildResponse(
            build_id=build_id,
            status=ThemeBuildStatus.QUEUED,
            message="Theme queued for rebuilding",
        ),
    )


@router.post(
    "/external/validate",
    response_model=SuccessResponse[ThemeValidationResponse],
    dependencies=[Depends(verify_store_ownership)],
)
async def validate_external_theme(
    store: Store = Depends(get_current_store),
) -> SuccessResponse[ThemeValidationResponse]:
    """Validate the bundle of the currently configured external theme."""
    import re

    theme_settings = store.theme_settings or {}
    external = theme_settings.get("external_theme")
    if not external or not external.get("bundle_url"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store does not have a bundled external theme installed",
        )

    bundle_url = external["bundle_url"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(bundle_url)
            resp.raise_for_status()
            content = resp.text
    except Exception as e:
        logger.error(f"Failed to fetch theme bundle for validation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch theme bundle for validation",
        )

    errors = []
    warnings = []

    DANGEROUS_PATTERNS = [
        (
            r"\beval\s*\(",
            "eval() calls are not allowed — potential code injection",
            "error",
        ),
        (
            r"new\s+Function\s*\(",
            "new Function() is not allowed — potential code injection",
            "error",
        ),
        (r"document\.cookie", "document.cookie access is not allowed", "error"),
        (
            r"\.innerHTML\s*=",
            "innerHTML assignment detected — potential XSS vector",
            "warning",
        ),
        (r"document\.write\s*\(", "document.write() is not allowed", "error"),
    ]

    for pattern, msg, severity in DANGEROUS_PATTERNS:
        if re.search(pattern, content):
            err = ValidationErrorModel(
                file="dist/theme.js", message=msg, severity=severity
            )
            if severity == "error":
                errors.append(err)
            else:
                warnings.append(err)

    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        errors.append(
            ValidationErrorModel(
                file="dist/theme.js",
                message="Bundle size exceeds maximum of 2MB",
                severity="error",
            )
        )

    return SuccessResponse(
        data=ThemeValidationResponse(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            contract_version=external.get("contract_version", "1.0"),
        )
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
