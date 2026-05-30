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
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import (
    get_current_store,
    get_store_repository,
    verify_store_ownership,
)
from src.api.dependencies.repositories import (
    get_store_theme_repository,
    get_theme_repository,
    get_theme_version_repository,
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
from src.application.services.theme_v3_presets import (
    generate_initial_v3_customization,
)
from src.core.entities.store import Store
from src.core.entities.theme import (
    Theme,
    ThemeStatus,
    ThemeType,
    ThemeVersion,
)
from src.infrastructure.cache.theme_build_store import get_theme_build_store
from src.infrastructure.repositories import StoreRepository
from src.infrastructure.repositories.store_theme_repository import (
    StoreThemeRepository,
)
from src.infrastructure.repositories.theme_repository import ThemeRepository
from src.infrastructure.repositories.theme_version_repository import (
    ThemeVersionRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/themes")


def _read_base_theme(theme_block) -> str | None:
    """Read the active base-theme id from a ``theme_settings.theme`` slot.

    Stores can hold one of two shapes:
      * ``"modern"`` — legacy form, the slot is a plain string id.
      * ``{"base_theme": "modern", ...}`` — canonical object form.

    Returns the id as a string for both, or ``None`` when the slot is
    missing or holds an unexpected type.
    """
    if isinstance(theme_block, str):
        return theme_block
    if isinstance(theme_block, dict):
        value = theme_block.get("base_theme")
        return value if isinstance(value, str) else None
    return None


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
    # Some legacy stores stored ``theme`` as a plain string (the
    # base-theme id directly) instead of the canonical
    # ``{"base_theme": "modern", ...}`` object. Coerce both shapes
    # to a string id so the response stays consistent and the route
    # doesn't AttributeError on ``str.get``.
    active_theme_id = _read_base_theme(theme_settings.get("theme"))

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
    theme_repo: ThemeRepository = Depends(get_theme_repository),
    version_repo: ThemeVersionRepository = Depends(get_theme_version_repository),
    store_theme_repo: StoreThemeRepository = Depends(get_store_theme_repository),
) -> SuccessResponse[ExternalThemeInfoResponse]:
    """Connect a local theme dev server URL to this store.

    The dev server should be running `numu-theme dev` (default port: 4321).
    The backend probes the dev server to verify it's reachable, then stores
    the URL in theme_settings.external_theme with mode="dev" so the storefront
    knows to bypass caching and always re-fetch the bundle.

    Used for the local development workflow:
    1. Developer runs `numu-theme dev` in their theme repo
    2. Developer pastes the URL (http://localhost:5173) into the dashboard
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
    # settings_schema.json is a Shopify-style *list* of setting defs in V3
    # themes; accept dict for legacy/wrapped shapes too.
    settings_schema: list | dict | None = None
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
        theme_settings["external_theme"]["merchant_settings"] = (
            preserved_merchant_settings
        )

    # Set base_theme to the external theme's ID so it becomes active immediately
    if "theme" not in theme_settings:
        theme_settings["theme"] = {}
    theme_settings["theme"]["base_theme"] = theme_id

    store.theme_settings = theme_settings
    await store_repo.update(store)

    # ── V3 wiring ────────────────────────────────────────────────────────
    # The V3 customizer reads from the `store_themes` table (via
    # StoreThemeRepository.get_active_for_store), not from
    # `theme_settings.external_theme`. Without these rows the editor shows
    # "No active theme for this store". Mirror what the marketplace
    # install/activate flow does, but for a dev-mode bundle.
    try:
        # 1. Theme row — upsert by slug. The slug must match manifest.id
        #    so subsequent reconnects (e.g. after a Vite restart) update
        #    the same row instead of inserting duplicates.
        theme_entity = await theme_repo.get_by_slug(theme_id)
        if theme_entity is None:
            theme_entity = Theme(
                id=uuid4(),
                name=manifest.get("name", theme_id),
                slug=theme_id,
                description=manifest.get("description"),
                author=manifest.get("author") or "Developer",
                type=ThemeType.EXTERNAL,
                status=ThemeStatus.PUBLISHED,
                is_public=False,
                settings_schema=settings_schema or {},
                section_schemas=sections_manifest or {},
                supported_features=manifest.get("supports"),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            theme_entity = await theme_repo.create(theme_entity)
        else:
            # Refresh schemas on every reconnect so the editor reflects
            # whatever the dev server is currently serving.
            theme_entity.settings_schema = settings_schema or {}
            theme_entity.section_schemas = sections_manifest or {}
            theme_entity.name = manifest.get("name", theme_id)
            theme_entity.description = manifest.get("description")
            theme_entity = await theme_repo.update(theme_entity)

        # 2. Fresh ThemeVersion per dev-connect. The dev URL doesn't have
        #    immutable content addressing so a stable version row would
        #    serve stale bundles after a rebuild.
        manifest_version = manifest.get("version", "0.0.0-dev")
        version_entity = ThemeVersion(
            id=uuid4(),
            theme_id=theme_entity.id,
            version=f"{manifest_version}+dev.{int(datetime.now(UTC).timestamp())}",
            bundle_url=bundle_url,
            css_url=css_url,
            manifest=manifest,
            is_latest=True,
            checksum="dev",  # No checksum — dev bundles aren't verified
            published_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        version_entity = await version_repo.create(version_entity)

        # 3. Delegate to ThemeActivationService — single source of truth
        #    for the snapshot → deactivate_all → upsert_active → mirror
        #    sequence. Phase 3a (2026-05-26) replaced ~70 lines of
        #    inline open-coded logic here with a service call that the
        #    marketplace activate path and V2 ThemeService also use.
        #    Behavior is identical: pre-dev-mode-switch / reconnect
        #    snapshot reason, customization seeded from manifest presets,
        #    is_active flipped on the matching store_themes row.
        from src.application.services.theme_activation_service import (
            ThemeActivationService,
        )
        from src.infrastructure.repositories.marketplace_repository import (
            MarketplaceRepository,
        )
        from src.infrastructure.repositories.store_theme_snapshot_repository import (
            StoreThemeSnapshotRepository,
        )

        snapshot_repo = StoreThemeSnapshotRepository(store_theme_repo.session)
        marketplace_repo = MarketplaceRepository(store_theme_repo.session)
        activation_svc = ThemeActivationService(
            store_theme_repo=store_theme_repo,
            snapshot_repo=snapshot_repo,
            marketplace_repo=marketplace_repo,
        )

        # Distinguish switch vs reconnect so the snapshot reason matches
        # the pre-refactor semantics (admin restore UI will eventually
        # surface this).
        prior_active = await store_theme_repo.get_active_for_store(store.id)
        snapshot_reason = (
            "pre-dev-mode-reconnect"
            if prior_active is not None and prior_active.theme_id == theme_entity.id
            else "pre-dev-mode-switch"
        )

        # Seed V3 customization from theme.json presets so the editor
        # opens to the developer's intended starting layout.
        v3_payload = generate_initial_v3_customization(
            theme_id=str(theme_entity.id),
            presets=manifest.get("presets") or {},
            bundle_url=bundle_url,
            css_url=css_url,
            settings_schema=settings_schema,
            section_schemas=sections_manifest,
            mode="development",  # localhost dev-mode bundle
        )
        v3_dict = v3_payload.model_dump()

        await activation_svc.activate(
            store_id=store.id,
            tenant_id=store.tenant_id,
            theme_id=theme_entity.id,
            theme_version_id=version_entity.id,
            reason=snapshot_reason,
            # Dev-mode is non-marketplace — clear any stale marketplace
            # install row so the storefront's two tables can't disagree.
            marketplace_theme_id=None,
            # Always reseed from manifest presets on a fresh dev-mode
            # connect; the developer's bundle is the source of truth for
            # the starting layout.
            seed_customization_v3=v3_dict,
        )
    except Exception as e:
        # The legacy `theme_settings.external_theme` was already saved
        # above, but without the V3 rows the customizer can't render
        # anything. Surface the failure so the merchant sees a clear error
        # and the underlying cause shows up in Sentry.
        logger.error("Dev-mode V3 seeding failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Dev server connected but V3 editor seeding failed: {e}",
        ) from e

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
    store.theme_settings = theme_settings
    await store_repo.update(store)

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
