"""In-app theme code-editor routes.

Mounted at /api/v1/stores/{store_id}/themes/code. Gives the merchant a real
file workspace (``store_theme_files``) to edit their theme source, then
Publish runs it through the SAME external-theme build pipeline that GitHub /
dev-server BYOT themes use (source="files").

Endpoints:
  GET    /{store_id}/themes/code/files                 — list workspace files
  GET    /{store_id}/themes/code/files/{path}          — read one file
  PUT    /{store_id}/themes/code/files/{path}          — create / overwrite file
  DELETE /{store_id}/themes/code/files/{path}          — delete file
  POST   /{store_id}/themes/code/scaffold              — seed a starter theme
  POST   /{store_id}/themes/code/publish               — build + publish workspace
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store, verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.theme import (
    ThemeBuildResponse,
    ThemeBuildStatus,
)
from src.api.v1.schemas.tenant.theme_v2 import (
    ScaffoldThemeRequest,
    ScaffoldThemeResponse,
    ThemeFileContentResponse,
    ThemeFileListResponse,
    ThemeFileMeta,
    WriteThemeFileRequest,
)
from src.application.services.theme_code_service import ThemeCodeService
from src.core.entities.store import Store
from src.core.entities.theme import StoreThemeFile
from src.infrastructure.cache.theme_build_store import get_theme_build_store
from src.infrastructure.repositories.store_theme_file_repository import (
    StoreThemeFileRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/themes/code")


def _get_code_svc(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ThemeCodeService:
    return ThemeCodeService(file_repo=StoreThemeFileRepository(session))


def _meta(f: StoreThemeFile) -> ThemeFileMeta:
    return ThemeFileMeta(
        path=f.path,
        size=len((f.content or "").encode("utf-8")),
        updated_at=f.updated_at.isoformat() if f.updated_at else None,
    )


@router.get(
    "/files",
    response_model=SuccessResponse[ThemeFileListResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="List the store's theme workspace files",
    tags=["Store Theme Code"],
)
async def list_files(
    store: Store = Depends(get_current_store),
    svc: ThemeCodeService = Depends(_get_code_svc),
) -> SuccessResponse[ThemeFileListResponse]:
    files = await svc.list_files(store.id)
    return SuccessResponse(
        data=ThemeFileListResponse(
            files=[_meta(f) for f in files],
            has_workspace=bool(files),
        )
    )


@router.get(
    "/files/{file_path:path}",
    response_model=SuccessResponse[ThemeFileContentResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Read one workspace file",
    tags=["Store Theme Code"],
)
async def read_file(
    file_path: str,
    store: Store = Depends(get_current_store),
    svc: ThemeCodeService = Depends(_get_code_svc),
) -> SuccessResponse[ThemeFileContentResponse]:
    f = await svc.read_file(store.id, file_path)
    return SuccessResponse(
        data=ThemeFileContentResponse(
            path=f.path,
            content=f.content,
            updated_at=f.updated_at.isoformat() if f.updated_at else None,
        )
    )


@router.put(
    "/files/{file_path:path}",
    response_model=SuccessResponse[ThemeFileContentResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Create or overwrite a workspace file",
    tags=["Store Theme Code"],
)
async def write_file(
    file_path: str,
    request: WriteThemeFileRequest,
    store: Store = Depends(get_current_store),
    svc: ThemeCodeService = Depends(_get_code_svc),
) -> SuccessResponse[ThemeFileContentResponse]:
    if store.tenant_id is None:
        raise HTTPException(status_code=400, detail="Store has no tenant_id")
    f = await svc.write_file(
        store_id=store.id,
        tenant_id=store.tenant_id,
        path=file_path,
        content=request.content,
    )
    return SuccessResponse(
        data=ThemeFileContentResponse(
            path=f.path,
            content=f.content,
            updated_at=f.updated_at.isoformat() if f.updated_at else None,
        ),
        message="File saved",
    )


@router.delete(
    "/files/{file_path:path}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(verify_store_ownership)],
    summary="Delete a workspace file",
    tags=["Store Theme Code"],
)
async def delete_file(
    file_path: str,
    store: Store = Depends(get_current_store),
    svc: ThemeCodeService = Depends(_get_code_svc),
) -> SuccessResponse[dict]:
    await svc.delete_file(store.id, file_path)
    return SuccessResponse(data={"deleted": True, "path": file_path})


@router.post(
    "/scaffold",
    response_model=SuccessResponse[ScaffoldThemeResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_store_ownership)],
    summary="Seed a buildable starter theme into the workspace",
    tags=["Store Theme Code"],
)
async def scaffold_theme(
    request: ScaffoldThemeRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    store: Store = Depends(get_current_store),
    svc: ThemeCodeService = Depends(_get_code_svc),
) -> SuccessResponse[ScaffoldThemeResponse]:
    if store.tenant_id is None:
        raise HTTPException(status_code=400, detail="Store has no tenant_id")

    # Seed from the store's active theme source when the client didn't pick a
    # source explicitly — so "Edit code" opens on the merchant's real theme
    # code. Unknown/un-bundled slugs fall back to v3_starter in the service.
    source = request.source
    if not source:
        try:
            from src.infrastructure.repositories.store_theme_repository import (
                StoreThemeRepository,
            )

            active = await StoreThemeRepository(session).get_active_for_store(store.id)
            source = getattr(active, "theme_slug", None) if active else None
        except Exception:
            source = None

    count = await svc.scaffold(
        store_id=store.id,
        tenant_id=store.tenant_id,
        theme_name=request.name,
        theme_id=request.theme_id,
        source=source,
        overwrite=request.overwrite,
    )
    return SuccessResponse(
        data=ScaffoldThemeResponse(
            file_count=count,
            message=f"Seeded a starter theme with {count} files.",
        ),
        message="Theme workspace scaffolded",
    )


@router.post(
    "/publish",
    response_model=SuccessResponse[ThemeBuildResponse],
    dependencies=[Depends(verify_store_ownership)],
    summary="Build & publish the code-editor workspace",
    tags=["Store Theme Code"],
)
async def publish_workspace(
    store: Store = Depends(get_current_store),
    svc: ThemeCodeService = Depends(_get_code_svc),
) -> SuccessResponse[ThemeBuildResponse]:
    """Run the workspace through the external-theme build pipeline.

    Identical downstream to a GitHub BYOT build — only the source differs
    (``source="files"`` materializes ``store_theme_files`` instead of cloning).
    Returns a build_id to poll at the existing
    ``GET /themes/external/builds/{build_id}`` endpoint.
    """
    files = await svc.list_files(store.id)
    if not files:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspace is empty. Scaffold or add files before publishing.",
        )

    build_id = uuid.uuid4().hex
    await get_theme_build_store().set(
        build_id,
        {
            "build_id": build_id,
            "status": ThemeBuildStatus.QUEUED,
            "store_id": str(store.id),
            "github_url": None,
            "branch": None,
            "theme_id": None,
            "bundle_url": None,
            "css_url": None,
            "error": None,
            "started_at": datetime.now(UTC),
            "completed_at": None,
        },
    )

    if store.tenant_id is None:
        raise HTTPException(status_code=400, detail="Store has no tenant_id")
    try:
        from src.infrastructure.messaging.tasks.theme_upload_tasks import (
            build_theme_from_files,
        )

        build_theme_from_files.delay(
            store_id=str(store.id),
            build_id=build_id,
            tenant_id=str(store.tenant_id),
        )
    except Exception as e:
        logger.error("Failed to dispatch code-editor build task: %s", e)
        await get_theme_build_store().update(
            build_id,
            {"status": ThemeBuildStatus.FAILED, "error": "Failed to queue build task"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Theme build service is temporarily unavailable",
        ) from e

    logger.info(
        "code_editor_build_queued",
        extra={"store_id": str(store.id), "build_id": build_id},
    )
    return SuccessResponse(
        data=ThemeBuildResponse(
            build_id=build_id,
            status=ThemeBuildStatus.QUEUED,
            message="Build queued. Poll the build status endpoint for updates.",
        ),
        message="Theme build queued successfully",
    )
