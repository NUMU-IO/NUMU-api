"""Theme ZIP upload + build status endpoints.

  POST /api/v1/themes/upload              — upload a theme ZIP (multipart)
  GET  /api/v1/themes/builds/{build_id}   — poll build status
  POST /api/v1/themes/preview/validate    — internal: Next.js validates preview tokens
  POST /api/v1/themes/{id}/preview        — generate a preview URL (authenticated)

Intended audience:
  - /upload: authenticated developers (any user role)
  - /builds/{id}: authenticated (owner of the build)
  - /preview/validate: called by Next.js with a preview token
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import tempfile
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from src.api.dependencies.auth import get_current_user_id
from src.api.responses import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/themes", tags=["Themes - Upload"])

MAX_ZIP_SIZE = 20 * 1024 * 1024  # 20 MB


# ── Schemas ───────────────────────────────────────────────────────────────────


class ThemeUploadResponse(BaseModel):
    build_id: str
    status: str
    poll_url: str


class BuildStatusResponse(BaseModel):
    build_id: str
    status: str
    theme_id: str | None = None
    version_id: str | None = None
    theme_slug: str | None = None
    version: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None
    error: str | None = None
    updated_at: str | None = None


class PreviewTokenRequest(BaseModel):
    token: str


class PreviewTokenResponse(BaseModel):
    installation_id: str
    theme_id: str
    version_id: str
    store_id: str


# ── Upload endpoint ───────────────────────────────────────────────────────────


@router.post(
    "/upload",
    response_model=SuccessResponse[ThemeUploadResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a theme ZIP for building",
)
async def upload_theme_zip(
    file: Annotated[UploadFile, File(description="Theme ZIP archive")],
    current_user_id: UUID = Depends(get_current_user_id),
) -> SuccessResponse[ThemeUploadResponse]:
    """Accept a theme ZIP, queue a build, return a build_id for polling.

    The zip must contain theme.json, settings_schema.json, styles.css,
    and an entry point (index.ts / index.tsx / numu.config.ts).
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "File must be a .zip archive")

    if file.content_type not in ("application/zip", "application/x-zip-compressed"):
        raise HTTPException(400, f"Invalid content type: {file.content_type}")

    # Read + size-check
    contents = await file.read()
    if len(contents) > MAX_ZIP_SIZE:
        raise HTTPException(
            413,
            f"ZIP too large: {len(contents) // 1024 // 1024}MB "
            f"(max {MAX_ZIP_SIZE // 1024 // 1024}MB)",
        )

    # Write to a temp location that the Celery worker can read.
    # Use the platform-specific temp dir (e.g. /tmp on Linux, %TEMP% on Windows)
    # so we don't hardcode a path. Override with NUMU_THEME_UPLOAD_DIR in prod
    # to point at a shared volume between API and worker hosts.
    default_upload_dir = os.path.join(tempfile.gettempdir(), "numu-theme-uploads")
    upload_dir = os.getenv("NUMU_THEME_UPLOAD_DIR", default_upload_dir)
    os.makedirs(upload_dir, exist_ok=True)
    sha = hashlib.sha256(contents).hexdigest()[:16]
    build_id = f"build_{sha}_{secrets.token_hex(4)}"
    zip_path = os.path.join(upload_dir, f"{build_id}.zip")
    with open(zip_path, "wb") as f:
        f.write(contents)

    # Queue the build
    from src.infrastructure.messaging.tasks.theme_upload_tasks import (
        _update_status,
        build_theme_from_zip,
    )

    _update_status(build_id, status="queued", uploader_id=str(current_user_id))
    build_theme_from_zip.delay(
        build_id=build_id,
        zip_path=zip_path,
        uploader_id=str(current_user_id),
    )

    logger.info(
        "theme_zip_upload_queued",
        extra={
            "build_id": build_id,
            "uploader_id": str(current_user_id),
            "size": len(contents),
        },
    )

    return SuccessResponse(
        data=ThemeUploadResponse(
            build_id=build_id,
            status="queued",
            poll_url=f"/api/v1/themes/builds/{build_id}",
        ),
        message="Theme upload accepted; build queued",
    )


# ── Build status polling ──────────────────────────────────────────────────────


@router.get(
    "/builds/{build_id}",
    response_model=SuccessResponse[BuildStatusResponse],
    summary="Poll theme build status",
)
async def get_build_status(
    build_id: str,
    current_user_id: UUID = Depends(get_current_user_id),
) -> SuccessResponse[BuildStatusResponse]:
    """Return the status of a theme build. Poll every 2s until status is
    `complete` or `failed`.
    """
    from src.infrastructure.messaging.tasks.theme_upload_tasks import (
        get_build_status as _get,
    )

    state = _get(build_id)
    if not state:
        raise HTTPException(404, f"Build {build_id} not found")

    return SuccessResponse(data=BuildStatusResponse(**state))


# ── Preview token validation ──────────────────────────────────────────────────

# In-memory preview token store (TODO: move to Redis in production).
# Each token maps to { installation_id, theme_id, version_id, store_id, user_id, expires_at }
_preview_tokens: dict[str, dict] = {}


def _cleanup_expired_tokens() -> None:
    """Remove expired preview tokens."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    expired = [
        token
        for token, data in _preview_tokens.items()
        if data.get("expires_at") and data["expires_at"] < now
    ]
    for token in expired:
        _preview_tokens.pop(token, None)


@router.post(
    "/preview/validate",
    response_model=SuccessResponse[PreviewTokenResponse],
    summary="Validate a preview token (internal — called by Next.js)",
)
async def validate_preview_token(
    request: PreviewTokenRequest,
) -> SuccessResponse[PreviewTokenResponse]:
    """Validate a preview token and return the preview context.

    Called by the Next.js storefront's `/api/preview` route to resolve
    which store + installation the preview belongs to.
    """
    _cleanup_expired_tokens()

    data = _preview_tokens.get(request.token)
    if not data:
        raise HTTPException(401, "Invalid or expired preview token")

    return SuccessResponse(
        data=PreviewTokenResponse(
            installation_id=data["installation_id"],
            theme_id=data["theme_id"],
            version_id=data["version_id"],
            store_id=data["store_id"],
        )
    )


# Export a helper so other modules can register preview tokens
def register_preview_token(
    installation_id: str,
    theme_id: str,
    version_id: str,
    store_id: str,
    user_id: str,
    ttl_seconds: int = 1800,
) -> str:
    """Create a preview token + store it. Returns the token string."""
    from datetime import UTC, datetime, timedelta

    token = secrets.token_urlsafe(32)
    _preview_tokens[token] = {
        "installation_id": installation_id,
        "theme_id": theme_id,
        "version_id": version_id,
        "store_id": store_id,
        "user_id": user_id,
        "expires_at": datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    }
    return token
