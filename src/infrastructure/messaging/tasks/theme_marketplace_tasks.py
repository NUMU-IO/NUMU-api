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
import random
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from src.core.entities.marketplace_theme import MarketplaceVersionStatus
from src.core.interfaces.services.storage_service import StorageBucket
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

T = TypeVar("T")


def _retry_with_backoff(
    operation: Callable[[], T],
    *,
    label: str,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
) -> T:
    """Retry an idempotent operation with exponential backoff + jitter.

    R2 / S3 occasionally returns 5xx during reroll periods; without retry
    the worker crashes mid-build, leaves the marketplace_theme_versions
    row in `building` forever, and the developer's only signal is the
    poll endpoint timing out. A trio of retries (1s, 2s, 4s + jitter)
    rides through nearly every transient.

    Re-raises the final exception on terminal failure so the calling
    task transitions to `build_failed` with a clear log entry.
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as e:  # noqa: BLE001 — we re-raise after attempts
            last = e
            if attempt == attempts:
                logger.error(
                    "%s: terminal failure after %d attempts: %s",
                    label,
                    attempts,
                    e,
                )
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = delay * (0.5 + random.random())  # ±50% jitter
            logger.warning(
                "%s: attempt %d/%d failed (%s) — retrying in %.1fs",
                label,
                attempt,
                attempts,
                e,
                delay,
            )
            time.sleep(delay)
    # Unreachable — loop either returns or raises.
    raise last  # type: ignore[misc]


logger = logging.getLogger(__name__)


import threading

_loop_lock = threading.Lock()
_thread_loops: dict[int, asyncio.AbstractEventLoop] = {}


def _get_or_create_thread_loop() -> asyncio.AbstractEventLoop:
    """Return a persistent event loop for the current worker thread.

    Why this exists: a Celery task calls `_run_async()` many times
    (load_version → mark_building → upload → mark_done). Creating a
    fresh `new_event_loop()` per call breaks asyncpg, which pins each
    connection to the loop that opened it — the engine's pool returns
    a connection from the previous (now-closed) loop and the next
    `await connection.send(...)` blows up with
    `'NoneType' object has no attribute 'send'`.

    Sharing one loop across calls keeps all asyncpg connections valid
    for the duration of the task (and the worker thread). The OS
    reaps the loop when the worker process exits — we never explicitly
    close it because closing it is what caused the original bug.
    """
    tid = threading.get_ident()
    with _loop_lock:
        loop = _thread_loops.get(tid)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            _thread_loops[tid] = loop
        return loop


def _run_async(coro):
    """Run an async coroutine from within a sync Celery task.

    Reuses a thread-local event loop so multiple `_run_async()` calls
    within the same task share one loop — required by asyncpg's
    loop-pinning. See `_get_or_create_thread_loop` for the why.
    """
    loop = _get_or_create_thread_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


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
        # Accept three shapes:
        #   1. Exact match — production submissions via `numu-theme submit`
        #      land here and require the developer to have bumped
        #      theme.json before publishing.
        #   2. Submitted version is `<theme.json.version>-dev.<X>` —
        #      `numu-theme install` auto-suffixes a `-dev.<timestamp>`
        #      tag so dev iterations never collide on the
        #      (theme_id, version_string) unique index. The base version
        #      stays the developer's intent.
        #   3. Submitted version is `<theme.json.version>+<X>` — semver
        #      build metadata; same intent as the dev suffix, kept for
        #      future tooling.
        # Anything else is a true mismatch and we abort.
        if manifest_version != version.version_string and not (
            isinstance(manifest_version, str)
            and (
                version.version_string.startswith(f"{manifest_version}-dev.")
                or version.version_string.startswith(f"{manifest_version}+")
            )
        ):
            raise ThemeBuildError(
                f"theme.json version ({manifest_version!r}) does not match "
                f"submitted version_string ({version.version_string!r}). "
                f"Allowed: exact match, '<base>-dev.<tag>', or '<base>+<tag>'."
            )

        # ── Build ─────────────────────────────────────────────────────────
        # Pre-built path: if the developer's CLI shipped a usable
        # dist/theme.js inside the ZIP, skip the worker's own build.
        # The dev-install loop relies on this because their package.json
        # uses `link:../numu-theme-sdk` (workspace-style references)
        # that the worker's vanilla `npm install` can't resolve. They
        # built locally where pnpm/links work; we trust that artifact
        # for *developer-self-install* use.
        #
        # Production marketplace submissions (`numu-theme submit`)
        # don't ship dist/, so this branch is bypassed and the worker
        # rebuilds from clean source — preserving the security property
        # that we never trust developer-machine-produced bundles for
        # public distribution.
        dist = theme_dir / "dist"
        prebuilt_bundle = dist / "theme.js"
        if prebuilt_bundle.exists() and prebuilt_bundle.stat().st_size > 0:
            logger.info(
                "marketplace_build_using_prebuilt",
                extra={
                    "version_id": version_id,
                    "size_bytes": prebuilt_bundle.stat().st_size,
                },
            )
        else:
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

        # Go through the factory so the local-filesystem fallback kicks
        # in when S3/R2 isn't configured (common for local dev — the
        # CloudflareR2StorageService class throws "Storage not configured"
        # otherwise). Production sets the s3_* vars and gets the real
        # R2 client transparently.
        from src.api.dependencies.services import get_storage_service

        storage = get_storage_service()

        bundle_key = (
            f"marketplace/{version.theme_id}/"
            f"{version.version_string}-{version_hash}/theme.js"
        )

        async def _upload_bundle():
            return await storage.upload_file(
                file_content=bundle_bytes,
                filename=bundle_key,
                content_type="application/javascript",
                bucket=StorageBucket.THEMES,
            )

        # R2 occasionally 5xxs during reroll; retry with backoff so a
        # transient outage doesn't strand the build in `building` state.
        bundle_uploaded = _retry_with_backoff(
            lambda: _run_async(_upload_bundle()),
            label=f"R2 upload theme.js for version {version.id}",
        )
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
                    bucket=StorageBucket.THEMES,
                )

            css_uploaded = _retry_with_backoff(
                lambda: _run_async(_upload_css()),
                label=f"R2 upload theme.css for version {version.id}",
            )
            css_url = css_uploaded.url

        # ── Phase 7.3 — static BYOT templates ─────────────────────────────
        # The theme's `theme.json` may declare `error_template` and
        # `loading_template` (conventionally `templates/error.html`
        # and `templates/loading.html`). The storefront fetches these
        # URLs at error/loading time and injects the HTML; absent →
        # falls back to platform chrome. Same R2 path scheme as the
        # bundle so the allowlist already covers them.
        def _upload_static_template(rel_path: str | None, suffix: str) -> str | None:
            if not rel_path or not isinstance(rel_path, str):
                return None
            tpl_path = theme_dir / rel_path
            if not tpl_path.exists():
                return None
            key = (
                f"marketplace/{version.theme_id}/"
                f"{version.version_string}-{version_hash}/templates/{suffix}.html"
            )

            async def _upload():
                return await storage.upload_file(
                    file_content=tpl_path.read_bytes(),
                    filename=key,
                    content_type="text/html; charset=utf-8",
                    bucket=StorageBucket.THEMES,
                )

            try:
                uploaded = _retry_with_backoff(
                    lambda: _run_async(_upload()),
                    label=f"R2 upload {suffix}.html for version {version.id}",
                )
                return uploaded.url
            except Exception as exc:
                logger.warning(
                    "marketplace_build_static_template_upload_failed",
                    extra={
                        "version_id": version_id,
                        "template": suffix,
                        "error": str(exc),
                    },
                )
                return None

        error_template_url = _upload_static_template(
            manifest.get("error_template"), "error"
        )
        loading_template_url = _upload_static_template(
            manifest.get("loading_template"), "loading"
        )

        # ── Extract schemas + presets ─────────────────────────────────────
        # The @numueg/theme-plugin embeds the canonical, MERGED schema set in
        # dist/manifest.json — settings_schema + section_schemas (a {type: def}
        # map, each section carrying its own `blocks`) + presets. That is the
        # authoritative source the editor consumes (themes.{settings_schema,
        # section_schemas} ← version row ← here), so prefer it. Fall back to
        # loose root files only for older/hand-authored themes that predate the
        # plugin's manifest embed. Columns are JSONB so list-or-dict is fine.
        #
        # Before this, the task read a loose `sections.json` that the plugin
        # never writes (per-section schemas live in schemas/sections/*.json and
        # are merged into dist/manifest.json) — so section_schemas came out {}
        # and the editor had nothing to render. See SESSION-T2A-AUDIT.md.
        settings_schema: list | dict = {}
        section_schemas: list | dict = {}
        presets: list | dict = {}

        dist_manifest = dist / "manifest.json"
        if dist_manifest.exists():
            try:
                dm = json.loads(dist_manifest.read_text(encoding="utf-8"))
                if isinstance(dm, dict):
                    settings_schema = dm.get("settings_schema") or settings_schema
                    section_schemas = dm.get("section_schemas") or section_schemas
                    presets = dm.get("presets") or presets
            except Exception as exc:
                logger.warning(
                    "marketplace_build_bad_manifest",
                    extra={"file": "dist/manifest.json", "error": str(exc)},
                )

        # Fallback for themes without a plugin manifest: loose root files,
        # filling only what the manifest didn't already provide.
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
                if target == "settings_schema" and not settings_schema:
                    settings_schema = data
                elif target == "section_schemas" and not section_schemas:
                    section_schemas = data
                elif target == "presets" and not presets:
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
                error_template_url=error_template_url,
                loading_template_url=loading_template_url,
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


# ── Watchdog: fail builds that have been "building" too long ─────────────────
#
# A worker can die mid-build (OOM, host reboot, R2 retry storm exhausted)
# in ways the try/except above can't catch. The marketplace_theme_versions
# row stays in `building` forever and the developer's poll endpoint never
# resolves. This beat task runs every few minutes and transitions any
# version that's been `building` for > BUILD_TIMEOUT_MINUTES into
# `build_failed` with a clear reason. The developer can then fix and
# resubmit.
#
# Schedule via celery beat:
#   "theme_marketplace_watchdog": {
#       "task": "theme_marketplace_watchdog",
#       "schedule": 300.0,  # every 5 min
#   }

BUILD_TIMEOUT_MINUTES = 15


@celery_app.task(name="theme_marketplace_watchdog")
def theme_marketplace_watchdog() -> dict[str, Any]:
    """Mark stale `building` marketplace versions as failed.

    Returns a small summary dict for logging / metrics. Idempotent — a
    version flipped to BUILD_FAILED here stays there; future runs skip
    it. Safe to run on every beat tick.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=BUILD_TIMEOUT_MINUTES)

    async def _sweep() -> dict[str, Any]:
        from sqlalchemy import select

        from src.infrastructure.database.connection import get_session
        from src.infrastructure.database.models.tenant.marketplace_theme import (
            MarketplaceThemeVersionModel,
        )

        async with get_session() as session:
            # Pick everything that's been BUILDING since before the cutoff.
            # `updated_at` advances on every status transition so this
            # excludes versions that are progressing.
            result = await session.execute(
                select(MarketplaceThemeVersionModel).where(
                    MarketplaceThemeVersionModel.status
                    == MarketplaceVersionStatus.BUILDING.value,
                    MarketplaceThemeVersionModel.updated_at < cutoff,
                )
            )
            stale = result.scalars().all()
            for row in stale:
                row.status = MarketplaceVersionStatus.BUILD_FAILED.value
                row.build_log = (
                    f"Build watchdog: version was in `building` for more "
                    f"than {BUILD_TIMEOUT_MINUTES} minutes and is presumed "
                    f"orphaned (worker crash, R2 outage, or process kill). "
                    f"Resubmit to retry."
                )
                row.updated_at = datetime.now(UTC)
            if stale:
                await session.commit()
            return {
                "swept": len(stale),
                "version_ids": [str(r.id) for r in stale],
            }

    summary = _run_async(_sweep())
    if summary["swept"]:
        logger.warning(
            "marketplace_watchdog_swept_orphans",
            extra=summary,
        )
    return summary
