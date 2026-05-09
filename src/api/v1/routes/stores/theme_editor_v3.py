"""V3 Theme Editor API routes.

All write endpoints perform Dual-Write: V3 columns + legacy columns.
Mounted at /stores/{store_id}/themes/v3/editor/

Endpoints:
  GET    /draft               — current draft (V3 if present; else normalized legacy)
  PUT    /autosave            — autosave a V3 payload (debounced from client)
  POST   /publish             — publish draft, dual-write, revalidate storefront
  POST   /discard             — drop the draft, revert to published
  GET    /versions            — paginated version history
  POST   /versions/{id}/restore — bring an older version back into the draft
  GET    /resolve             — published settings (no draft) for storefront SDKs
  GET    /schemas             — section/block schemas for the active theme
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import ValidationError

from src.api.dependencies import (
    get_current_user_id,
    get_store_repository,
    verify_store_ownership,
)
from src.api.dependencies.repositories import (
    get_store_theme_repository,
    get_theme_customization_version_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.theme_v3 import (
    MAX_CUSTOMIZATION_BYTES,
    AutosaveDraftRequest,
    AutosaveDraftResponse,
    DiscardDraftResponse,
    PublishResponse,
    SchemaResponse,
    VersionListResponse,
    VersionPayloadResponse,
    customization_payload_size,
)
from src.application.services.theme_v3_service import StaleEtagError, ThemeV3Service
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.repositories.theme_customization_version_repository import (
    ThemeCustomizationVersionRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/{store_id}/themes/v3/editor",
    tags=["Theme Editor V3"],
    dependencies=[Depends(verify_store_ownership)],
)


def _get_v3_service(
    store_theme_repo: Annotated[
        StoreThemeRepository, Depends(get_store_theme_repository)
    ],
    version_repo: Annotated[
        ThemeCustomizationVersionRepository,
        Depends(get_theme_customization_version_repository),
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> ThemeV3Service:
    return ThemeV3Service(
        store_theme_repo=store_theme_repo,
        version_repo=version_repo,
        store_repo=store_repo,
    )


# ── Draft ────────────────────────────────────────────────────────────────────


@router.get("/draft")
async def get_draft(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    response: Response,
):
    """Get the current V3 draft for the active theme.

    Returns the draft as the response data and emits an HTTP `ETag`
    header so the client can echo it on the next autosave (`If-Match`).
    Mismatch on autosave → 409. This is the standard optimistic-
    concurrency pattern; using a real header keeps the JSON payload
    backwards compatible with older hub builds.
    """
    data = await svc.get_draft_with_etag(store_id)
    if data["etag"]:
        response.headers["ETag"] = data["etag"]
    return SuccessResponse(data=data["draft"], message="Draft retrieved")


@router.put(
    "/autosave",
    response_model=SuccessResponse[AutosaveDraftResponse],
)
async def autosave_draft(
    store_id: UUID,
    body: AutosaveDraftRequest,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    """Auto-save V3 draft with Dual-Write to legacy columns.

    Idempotent if the payload is unchanged from the last draft.
    Pydantic re-validates the payload (incl. external_theme URL allowlist)
    before any DB write.

    Rejects oversized payloads with 413 (Payload Too Large) before the
    service layer touches the DB. JSONB can technically hold ~1 GB, but
    a customization that big means runaway preset duplication or embedded
    data URLs and the autosave debouncer can't keep up.

    Optimistic concurrency: when the client sends `If-Match: <etag>`
    (the etag from the most recent /draft fetch or a previous
    autosave), we compare to the current store_theme.updated_at. On
    mismatch → 409 with the current etag + draft so the client can
    surface "another tab is editing; reload to continue". When the
    request body carries `expected_etag`, that wins over the header
    (kept for clients that prefer body-only payloads).
    """
    size = customization_payload_size(body.payload)
    if size > MAX_CUSTOMIZATION_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"customization payload too large: {size} bytes "
                f"(limit {MAX_CUSTOMIZATION_BYTES}). "
                "Reduce embedded asset sizes (consider image_picker URLs "
                "instead of inline data: URIs) or split into more sections."
            ),
        )
    expected_etag = body.expected_etag or if_match
    try:
        data = await svc.autosave_draft(
            store_id=store_id,
            payload=body.payload,
            user_id=user_id,
            change_summary=body.change_summary or "Auto-save",
            expected_etag=expected_etag,
        )
    except StaleEtagError as e:
        # Surface current draft + new etag so the client can prompt the
        # merchant to reload, or (in future) merge.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stale_etag",
                "message": str(e),
                "current_etag": e.current_etag,
                "current_draft": e.current_draft,
            },
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.errors(include_url=False),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Echo the new etag so the client uses it for the next autosave.
    new_state = await svc.get_draft_with_etag(store_id)
    if new_state["etag"]:
        response.headers["ETag"] = new_state["etag"]
    return SuccessResponse(
        data=AutosaveDraftResponse(draft=data), message="Draft saved"
    )


# ── Publish ──────────────────────────────────────────────────────────────────


@router.post("/publish", response_model=SuccessResponse[PublishResponse])
async def publish(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Publish V3 draft with Dual-Write to all columns.

    Triggers Next.js ISR cache invalidation after successful publish
    (non-fatal if the storefront is unreachable).
    """
    try:
        data = await svc.publish(store_id=store_id, user_id=user_id)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.errors(include_url=False),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(
        data=PublishResponse(published=data), message="Published successfully"
    )


# ── Version history ──────────────────────────────────────────────────────────


@router.get("/versions", response_model=SuccessResponse[VersionListResponse])
async def list_versions(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List version history for the store's theme customization."""
    versions = await svc.get_versions(store_id=store_id, page=page, per_page=per_page)
    return SuccessResponse(
        data=VersionListResponse(versions=versions, page=page, per_page=per_page),
        message="Versions retrieved",
    )


@router.post(
    "/versions/{version_id}/restore",
    response_model=SuccessResponse[AutosaveDraftResponse],
)
async def restore_version(
    store_id: UUID,
    version_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Restore a previous version as the current draft."""
    try:
        data = await svc.restore_version(
            store_id=store_id, version_id=version_id, user_id=user_id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(
        data=AutosaveDraftResponse(draft=data), message="Version restored"
    )


@router.get(
    "/versions/{version_id}",
    response_model=SuccessResponse[VersionPayloadResponse],
)
async def get_version_payload(
    store_id: UUID,
    version_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
):
    """Return a single version's stored payload (read-only).

    Used by the merchant hub's Version-Diff dialog to load two snapshots
    side-by-side without touching the live draft. 404s when the
    version doesn't belong to this store — same shape as
    /versions/{id}/restore so version ids can't be probed across stores.
    """
    try:
        payload = await svc.get_version_payload(
            store_id=store_id, version_id=version_id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(
        data=VersionPayloadResponse(payload=payload),
        message="Version payload retrieved",
    )


# ── Discard draft ────────────────────────────────────────────────────────────


@router.post("/discard", response_model=SuccessResponse[DiscardDraftResponse])
async def discard_draft(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
):
    """Discard V3 draft and revert to published state."""
    try:
        data = await svc.discard_draft(store_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(
        data=DiscardDraftResponse(published=data), message="Draft discarded"
    )


# ── Schemas ──────────────────────────────────────────────────────────────────


@router.get("/schemas", response_model=SuccessResponse[SchemaResponse])
async def get_schemas(
    store_id: UUID,
    store_theme_repo: Annotated[
        StoreThemeRepository, Depends(get_store_theme_repository)
    ],
):
    """Get the active theme's section/block schemas.

    For built-in themes: reads from the theme's settings_schema and
    section_schemas. For BYOT themes: reads from the marketplace theme
    version's schema columns.
    """
    store_theme = await store_theme_repo.get_active_for_store(store_id)
    if not store_theme:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active theme for this store",
        )

    return SuccessResponse(
        data=SchemaResponse(
            theme_id=str(store_theme.theme_id),
            theme_slug=store_theme.theme_slug,
            settings_schema=store_theme.settings_schema or {},
            section_schemas=store_theme.section_schemas or {},
            theme_type=(
                store_theme.theme_type.value if store_theme.theme_type else "internal"
            ),
        ),
        message="Schemas retrieved",
    )


# ── Resolve (storefront SDK) ─────────────────────────────────────────────────


@router.get("/resolve")
async def resolve_theme(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
):
    """Resolve the published theme settings using Dual-Read normalization.

    Distinct from `/draft`: this endpoint never returns draft data. It is
    intended for the storefront SDK to render the live theme. Returns V3
    published data if present; otherwise normalizes V1/V2 published →
    V3 in memory.
    """
    data = await svc.get_published(store_id)
    return SuccessResponse(data=data, message="Theme resolved")
