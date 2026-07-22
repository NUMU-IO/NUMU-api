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
import os
import random
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from src.core.entities.marketplace_theme import MarketplaceVersionStatus
from src.core.interfaces.services.storage_service import StorageBucket
from src.core.theme_contract import validate_navigability_source
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
    resolve_uploaded_zip,
)

T = TypeVar("T")

# ── Certification lint gate ──────────────────────────────────────────────────
# The theme CLI owns the 12 lint rules. We shell out to it rather than porting
# them, because a second implementation is a second thing to drift -- exactly
# the duplication problem the shared-primitive work exists to remove.
#
# NUMU_THEME_LINT_GATE:
#   "enforce" — error-severity issues fail the build (recommended)
#   "warn"    — record the result, never block (roll-out mode)
#   "off"     — don't run it at all
# NUMU_THEME_CLI_BIN may point at a `numu-theme` executable; otherwise we try
# the locally-installed CLI via npx without letting it reach the network.
LINT_GATE_MODE = os.getenv("NUMU_THEME_LINT_GATE", "warn").lower()
THEME_CLI_BIN = os.getenv("NUMU_THEME_CLI_BIN", "").strip()
LINT_TIMEOUT_SECONDS = 120


def _lint_candidates() -> list[list[str]]:
    """Command forms to try, most explicit first."""
    if THEME_CLI_BIN:
        return [[THEME_CLI_BIN]]
    # --no-install keeps this offline: if the CLI isn't already present we
    # report `unavailable` rather than silently pulling code from the network
    # into the build path.
    return [["npx", "--no-install", "numu-theme"], ["numu-theme"]]


def _lint_theme(theme_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """Run the theme CLI's lint rules over extracted theme source.

    Returns ``(status, issues)`` where status is passed | failed | unavailable.
    A linter that cannot run yields `unavailable`, never `passed` -- an absent
    gate must not be indistinguishable from a satisfied one.
    """
    last_err: str | None = None
    for cmd in _lint_candidates():
        try:
            result = subprocess.run(
                [*cmd, "lint", "--json", "--dir", str(theme_dir)],
                capture_output=True,
                text=True,
                timeout=LINT_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            last_err = str(exc)
            continue

        # The CLI exits 1 when it finds errors, so a non-zero code is a normal
        # outcome; only unparseable output means we failed to run it.
        try:
            issues = json.loads(result.stdout).get("issues", [])
        except (json.JSONDecodeError, AttributeError):
            last_err = (result.stderr or result.stdout or "no output")[:300]
            continue

        errors = [i for i in issues if i.get("severity") == "error"]
        return ("failed" if errors else "passed"), issues

    logger.warning(
        "theme_lint_unavailable",
        extra={"theme_dir": str(theme_dir), "error": last_err},
    )
    return "unavailable", []


def _certification_tier(lint_status: str, issues: list[dict[str, Any]]) -> str:
    """Map a lint outcome onto the published certification tier."""
    if lint_status != "passed":
        return "legacy"
    return "compatible" if issues else "certified"


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

        # Containment re-check at read time (defense in depth — submit_version
        # validates too, but rows written before that validation existed, or
        # by any other writer, still get refused here).
        try:
            zip_path = resolve_uploaded_zip(version.source_zip_path)
        except ValueError as exc:
            raise ThemeBuildError(str(exc)) from exc
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
        # Pre-built path: `numu-theme install` (the developer self-install
        # loop) ships a locally-built dist/theme.js because its package.json
        # uses `link:../numu-theme-sdk` (workspace-style references) that
        # the worker's vanilla `npm install` can't resolve. Those versions
        # are identifiable by the `-dev.<tag>` suffix the CLI appends, stay
        # scoped to the developer's own stores, and are refused marketplace
        # approval outright (see review_version).
        #
        # Every OTHER submission — anything marketplace-shaped — is rebuilt
        # from clean source even when the ZIP ships a dist/: developer-
        # machine-produced bundles are never trusted for public
        # distribution, whether or not the client was well-behaved.
        dist = theme_dir / "dist"
        prebuilt_bundle = dist / "theme.js"
        is_dev_install = "-dev." in version.version_string
        used_prebuilt = (
            is_dev_install
            and prebuilt_bundle.exists()
            and prebuilt_bundle.stat().st_size > 0
        )
        if used_prebuilt:
            logger.info(
                "marketplace_build_using_prebuilt",
                extra={
                    "version_id": version_id,
                    "size_bytes": prebuilt_bundle.stat().st_size,
                },
            )
        else:
            if dist.exists():
                # Discard any shipped dist/ wholesale before rebuilding so
                # stale developer artifacts can't leak into the fresh build.
                shutil.rmtree(dist)
                logger.info(
                    "marketplace_build_discarded_prebuilt",
                    extra={"version_id": version_id},
                )
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

        # ── Certification lint gate ───────────────────────────────────────
        # Runs against the extracted SOURCE (the rules read theme.json,
        # settings_schema.json, locales and section components -- none of
        # which survive bundling). Until this existed, the 12 lint rules ran
        # only if a developer chose to run them locally; nothing on the path
        # to publication checked anything.
        if LINT_GATE_MODE == "off":
            lint_status, lint_issues = "skipped", []
        else:
            lint_status, lint_issues = _lint_theme(theme_dir)

            # Belt and braces for guarantee G2 (navigability): the CLI owns
            # the rule, but an old or unavailable CLI must not let a
            # chrome-less theme read as `passed` — re-derive it here from the
            # same source schemas. A guarantee violation is a lint failure
            # even when the linter itself couldn't run.
            if not any(i.get("rule") == "navigability" for i in lint_issues):
                nav_errors = validate_navigability_source(theme_dir)
                if nav_errors:
                    lint_issues = [
                        *lint_issues,
                        *(
                            {
                                "rule": "navigability",
                                "severity": "error",
                                "message": msg,
                            }
                            for msg in nav_errors
                        ),
                    ]
                    if lint_status in ("passed", "unavailable"):
                        lint_status = "failed"

        lint_errors = [i for i in lint_issues if i.get("severity") == "error"]
        if LINT_GATE_MODE == "enforce" and lint_status != "passed":
            if lint_status == "unavailable":
                raise ThemeBuildError(
                    "certification lint could not run and the gate is set to "
                    "enforce; install @numueg/theme-cli on the build host or "
                    "set NUMU_THEME_LINT_GATE=warn"
                )
            detail = "; ".join(
                f"{i.get('rule', '?')}: {i.get('message', '')}" for i in lint_errors[:5]
            )
            raise ThemeBuildError(f"certification lint failed: {detail}")

        tier = _certification_tier(lint_status, lint_issues)
        logger.info(
            "marketplace_build_lint",
            extra={
                "version_id": version_id,
                "lint_status": lint_status,
                "errors": len(lint_errors),
                "warnings": len(lint_issues) - len(lint_errors),
                "tier": tier,
                "mode": LINT_GATE_MODE,
            },
        )

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
                lint_status=lint_status,
                lint_issues={"issues": lint_issues},
                certification_tier=tier,
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

        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.database.models.tenant.marketplace_theme import (
            MarketplaceThemeVersionModel,
        )

        async with AsyncSessionLocal() as session:
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
