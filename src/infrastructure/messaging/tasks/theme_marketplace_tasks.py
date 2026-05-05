"""Celery task for marketplace theme builds.

Reuses the existing sandboxed build pipeline from `theme_upload_tasks`
(safe ZIP extraction, contract validation, optional Docker isolation,
AST-based security scan, R2 upload) and writes the result into the
`marketplace_theme_versions` row.

Lifecycle for a marketplace version:

    pending_build -> building -> pending_review -> (approved -> published)
                                                or (rejected)

A failure in any stage transitions the version to `build_failed` and
captures the error in `build_log`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from src.core.entities.marketplace_theme import MarketplaceVersionStatus
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.messaging.tasks.theme_upload_tasks import (
    MAX_BUNDLE_SIZE,
    USE_DOCKER,
    ThemeBuildError,
    _ast_security_scan,
    _run_in_docker,
    _run_local_build,
    _safe_extract_zip,
    _validate_theme_contract,
)

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from within a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _update_version_status(version_id: UUID, **fields) -> None:
    """Persist a status update on the marketplace_theme_versions row."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.marketplace_repository import (
        MarketplaceRepository,
    )

    async with AsyncSessionLocal() as session:
        repo = MarketplaceRepository(session)
        await repo.update_version(version_id, fields)
        await session.commit()


async def _load_version(version_id: UUID):
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.marketplace_repository import (
        MarketplaceRepository,
    )

    async with AsyncSessionLocal() as session:
        repo = MarketplaceRepository(session)
        return await repo.get_version_by_id(version_id)


@celery_app.task(
    name="build_marketplace_theme",
    bind=True,
    max_retries=0,  # Surface failures to the developer; they can resubmit
    soft_time_limit=300,
    time_limit=360,
)
def build_marketplace_theme(self, version_id: str) -> dict:
    """Build a marketplace theme version end-to-end.

    Reads `source_zip_path` from the version row, runs the sandboxed
    build pipeline, uploads to R2, and stamps bundle_url/css_url/checksum
    plus presets/schemas extracted from the theme source.
    """
    vid = UUID(version_id)
    work_dir: Path | None = None

    try:
        version = _run_async(_load_version(vid))
        if not version:
            raise ThemeBuildError(f"version {version_id} not found")
        if not version.source_zip_path:
            raise ThemeBuildError("version has no source_zip_path")

        zip_path = Path(version.source_zip_path)
        if not zip_path.exists():
            raise ThemeBuildError(f"source ZIP missing: {zip_path}")

        # ── Mark in-progress ──────────────────────────────────────────────
        _run_async(
            _update_version_status(
                vid,
                status=MarketplaceVersionStatus.BUILDING.value,
                build_log="Building…",
            )
        )

        # ── Extract ───────────────────────────────────────────────────────
        work_dir = Path(tempfile.mkdtemp(prefix="numu-mkt-build-"))
        theme_dir = work_dir / "theme"
        theme_dir.mkdir(parents=True)
        _safe_extract_zip(zip_path, theme_dir)

        # ── Validate contract ─────────────────────────────────────────────
        manifest = _validate_theme_contract(theme_dir)
        manifest_version = manifest.get("version")
        if manifest_version != version.version_string:
            raise ThemeBuildError(
                f"theme.json version ({manifest_version!r}) does not match "
                f"submitted version_string ({version.version_string!r})"
            )

        # ── Build ─────────────────────────────────────────────────────────
        dist = theme_dir / "dist"
        dist.mkdir(exist_ok=True)
        result = (
            _run_in_docker(theme_dir) if USE_DOCKER else _run_local_build(theme_dir)
        )
        if result.returncode != 0:
            raise ThemeBuildError(
                f"build command failed: {result.stderr.strip()[:500]}"
            )

        bundle_path: Path | None = None
        for name in ("theme.js", "theme.mjs", "theme.esm.js"):
            cand = dist / name
            if cand.exists():
                bundle_path = cand
                break
        if not bundle_path:
            raise ThemeBuildError("build produced no bundle (dist/theme.js)")

        # ── Size check + AST security scan ────────────────────────────────
        size = bundle_path.stat().st_size
        if size > MAX_BUNDLE_SIZE:
            raise ThemeBuildError(
                f"bundle too large: {size / 1024 / 1024:.1f}MB (max "
                f"{MAX_BUNDLE_SIZE // 1024 // 1024}MB)"
            )

        violations = _ast_security_scan(bundle_path)
        if violations:
            raise ThemeBuildError(f"security scan failed: {'; '.join(violations[:5])}")

        # ── Upload to R2 ──────────────────────────────────────────────────
        bundle_bytes = bundle_path.read_bytes()
        checksum = hashlib.sha256(bundle_bytes).hexdigest()
        version_hash = checksum[:8]

        from src.infrastructure.external_services.cloudflare_r2.storage_service import (
            CloudflareR2StorageService,
        )

        storage = CloudflareR2StorageService()

        bundle_key = (
            f"marketplace/{version.theme_id}/"
            f"{version.version_string}-{version_hash}/theme.js"
        )

        async def _upload_bundle():
            return await storage.upload_file(
                file_content=bundle_bytes,
                filename=bundle_key,
                content_type="application/javascript",
                bucket="themes",
            )

        bundle_uploaded = _run_async(_upload_bundle())
        bundle_url = bundle_uploaded.url

        css_url: str | None = None
        css_path = dist / "theme.css"
        if css_path.exists():
            css_key = (
                f"marketplace/{version.theme_id}/"
                f"{version.version_string}-{version_hash}/theme.css"
            )

            async def _upload_css():
                return await storage.upload_file(
                    file_content=css_path.read_bytes(),
                    filename=css_key,
                    content_type="text/css",
                    bucket="themes",
                )

            css_uploaded = _run_async(_upload_css())
            css_url = css_uploaded.url

        # ── Extract schemas + presets ─────────────────────────────────────
        settings_schema: dict = {}
        section_schemas: dict = {}
        presets: dict = {}

        for fname, target in (
            ("settings_schema.json", "settings_schema"),
            ("sections.json", "section_schemas"),
            ("presets.json", "presets"),
        ):
            f = theme_dir / fname
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if target == "settings_schema":
                    settings_schema = data
                elif target == "section_schemas":
                    section_schemas = data
                else:
                    presets = data
            except Exception as exc:
                logger.warning(
                    "marketplace_build_bad_json",
                    extra={"file": fname, "error": str(exc)},
                )

        # ── Persist success: pending_review for admin moderation ──────────
        _run_async(
            _update_version_status(
                vid,
                status=MarketplaceVersionStatus.PENDING_REVIEW.value,
                bundle_url=bundle_url,
                css_url=css_url,
                size_bytes=size,
                checksum=checksum,
                settings_schema=settings_schema,
                section_schemas=section_schemas,
                presets=presets,
                build_log=f"Build succeeded at {datetime.now(UTC).isoformat()}",
            )
        )

        logger.info(
            "marketplace_build_succeeded",
            extra={
                "version_id": version_id,
                "theme_id": str(version.theme_id),
                "size_bytes": size,
                "checksum": checksum,
            },
        )
        return {
            "version_id": version_id,
            "status": MarketplaceVersionStatus.PENDING_REVIEW.value,
            "bundle_url": bundle_url,
            "css_url": css_url,
            "size_bytes": size,
            "checksum": checksum,
        }

    except ThemeBuildError as exc:
        logger.warning(
            "marketplace_build_failed",
            extra={"version_id": version_id, "error": str(exc)},
        )
        _run_async(
            _update_version_status(
                vid,
                status=MarketplaceVersionStatus.BUILD_FAILED.value,
                build_log=str(exc)[:5000],
            )
        )
        return {
            "version_id": version_id,
            "status": MarketplaceVersionStatus.BUILD_FAILED.value,
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover — unexpected errors
        logger.exception(
            "marketplace_build_unexpected_error",
            extra={"version_id": version_id},
        )
        try:
            _run_async(
                _update_version_status(
                    vid,
                    status=MarketplaceVersionStatus.BUILD_FAILED.value,
                    build_log=f"Unexpected error: {exc!r}"[:5000],
                )
            )
        except Exception:
            pass
        raise
    finally:
        if work_dir is not None:
            import shutil

            shutil.rmtree(work_dir, ignore_errors=True)
