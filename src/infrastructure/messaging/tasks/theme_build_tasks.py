"""Celery tasks for building external themes (BYOT).

This task handles the full pipeline:
1. Clone the GitHub repo (shallow)
2. Validate the theme contract
3. Run npm install + build
4. Upload outputs (theme.esm.js + theme.css) to CDN
5. Update the store's theme_settings with CDN URLs
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

# Maximum allowed bundle size: 2MB
MAX_BUNDLE_SIZE = 2 * 1024 * 1024

# Dangerous patterns in built JS (basic security scan)
DANGEROUS_PATTERNS = [
    "eval(",
    "new Function(",
    "document.cookie",
    "document.write(",
]


def _update_build_status(build_id: str, **kwargs: object) -> None:
    """Update the in-memory build status.

    In production, this should update Redis or the database.
    """
    from src.api.v1.routes.stores.themes import _build_statuses

    if build_id in _build_statuses:
        _build_statuses[build_id].update(kwargs)


def _validate_theme_json(theme_dir: Path) -> dict:
    """Validate that theme.json exists and has required fields."""
    theme_json_path = theme_dir / "theme.json"
    if not theme_json_path.exists():
        raise ValueError("Missing required file: theme.json")

    with open(theme_json_path) as f:
        manifest = json.load(f)

    required_fields = ["id", "name", "nameAr", "layout", "version"]
    missing = [f for f in required_fields if f not in manifest]
    if missing:
        raise ValueError(f"theme.json missing required fields: {', '.join(missing)}")

    # Validate ID format
    theme_id = manifest["id"]
    if not isinstance(theme_id, str) or not theme_id.strip():
        raise ValueError("theme.json: 'id' must be a non-empty string")

    return manifest


def _materialize_store_files(store_id: str, dest: Path) -> None:
    """Write a store's code-editor workspace (store_theme_files) onto disk.

    This is the file-store equivalent of ``git clone`` — it lays the merchant's
    edited theme source into ``dest`` so the rest of the pipeline (validate →
    install → build → upload) runs unchanged. Raises if the workspace is empty
    so the build fails fast with a clear message instead of "missing theme.json".
    """
    import asyncio
    from uuid import UUID

    from src.infrastructure.database.connection import AsyncSessionLocal, engine
    from src.infrastructure.repositories.store_theme_file_repository import (
        StoreThemeFileRepository,
    )

    dest_root = dest.resolve()

    async def _run() -> int:
        # Dispose the engine pool on the way out so a later new_event_loop()
        # block in the same sync task (e.g. _register) doesn't reuse a
        # connection bound to this now-closed loop (asyncpg cross-loop crash).
        try:
            async with AsyncSessionLocal() as session:
                repo = StoreThemeFileRepository(session)
                files = await repo.list_for_store(UUID(store_id))
                for f in files:
                    # Defense-in-depth against path traversal in stored paths.
                    target = (dest_root / f.path.lstrip("/")).resolve()
                    if dest_root not in target.parents and target != dest_root:
                        raise ValueError(f"Unsafe theme file path: {f.path}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(f.content, encoding="utf-8")
                return len(files)
        finally:
            await engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        count = loop.run_until_complete(_run())
    finally:
        loop.close()

    if count == 0:
        raise ValueError(
            "No theme files to build. Scaffold or save files in the code "
            "editor before publishing."
        )


def _validate_required_files(theme_dir: Path) -> None:
    """Validate that all required theme files exist."""
    required = [
        "theme.json",
        "settings_schema.json",
        "styles.css",
    ]
    # Entry point: index.ts or numu.config.ts
    has_entry = (
        (theme_dir / "index.ts").exists()
        or (theme_dir / "index.tsx").exists()
        or (theme_dir / "numu.config.ts").exists()
    )
    if not has_entry:
        raise ValueError("Missing entry point: need index.ts or numu.config.ts")

    for f in required:
        if not (theme_dir / f).exists():
            raise ValueError(f"Missing required file: {f}")


def _security_scan_bundle(bundle_path: Path) -> list[str]:
    """Basic security scan of the built bundle for dangerous patterns."""
    content = bundle_path.read_text(encoding="utf-8", errors="replace")
    violations = []
    for pattern in DANGEROUS_PATTERNS:
        if pattern in content:
            violations.append(f"Dangerous pattern found: {pattern}")
    return violations


@celery_app.task(
    name="build_external_theme",
    bind=True,
    max_retries=1,
    soft_time_limit=300,  # 5 minutes
    time_limit=360,  # 6 minutes hard limit
)
def build_external_theme(
    self,
    store_id: str,
    github_url: str,
    branch: str,
    build_id: str,
    source: str = "github",
) -> dict:
    """Build an external theme and publish it to the store.

    ``source`` selects where the theme source comes from:
    - ``"github"`` (default): shallow-clone ``github_url`` (the BYOT flow).
    - ``"files"``: materialize the store's in-app code-editor workspace
      (``store_theme_files``) onto disk. ``github_url`` is ignored.

    Everything after source acquisition is identical for both — that's the
    whole point: the code editor reuses the exact same pipeline.

    Steps:
    1. Acquire source (clone OR materialize file store)
    2. Validate theme contract (theme.json, settings_schema.json, etc.)
    3. npm install (with --ignore-scripts for security)
    4. npm run build (expects @numu/theme-plugin in vite.config — validates
       the contract, externalizes the SDK, emits dist/manifest.json)
    5. Security-scan the output bundle
    6. Upload to R2/S3
    7. Update store's theme_settings
    """
    work_dir = None

    try:
        # ── Step 1: Acquire source ───────────────────────────────────────
        _update_build_status(build_id, status="cloning")
        work_dir = Path(tempfile.mkdtemp(prefix="numu-theme-"))
        theme_dir = work_dir / "theme"

        if source == "files":
            logger.info("Materializing theme files from store %s", store_id)
            theme_dir.mkdir(parents=True, exist_ok=True)
            _materialize_store_files(store_id, theme_dir)
        else:
            logger.info("Cloning theme repo: %s (branch: %s)", github_url, branch)
            clone_result = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth=1",
                    f"--branch={branch}",
                    github_url,
                    str(theme_dir),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if clone_result.returncode != 0:
                raise ValueError(f"Git clone failed: {clone_result.stderr.strip()}")

        # ── Step 2: Validate ─────────────────────────────────────────────
        _update_build_status(build_id, status="validating")
        logger.info("Validating theme contract")

        _validate_required_files(theme_dir)
        manifest = _validate_theme_json(theme_dir)
        theme_id = manifest["id"]

        _update_build_status(build_id, theme_id=theme_id)

        # ── Step 3: Install dependencies ─────────────────────────────────
        _update_build_status(build_id, status="building")
        logger.info("Installing dependencies for theme: %s", theme_id)

        # Determine package manager
        has_bun_lock = (theme_dir / "bun.lock").exists() or (
            theme_dir / "bun.lockb"
        ).exists()
        has_pnpm_lock = (theme_dir / "pnpm-lock.yaml").exists()

        if has_bun_lock:
            install_cmd = ["bun", "install", "--frozen-lockfile"]
        elif has_pnpm_lock:
            install_cmd = ["pnpm", "install", "--frozen-lockfile"]
        else:
            install_cmd = ["npm", "install", "--ignore-scripts"]

        install_result = subprocess.run(
            install_cmd,
            cwd=str(theme_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )

        if install_result.returncode != 0:
            raise ValueError(
                f"Dependency install failed: {install_result.stderr.strip()[:500]}"
            )

        # ── Step 4: Build ────────────────────────────────────────────────
        logger.info("Building theme: %s", theme_id)

        build_result = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(theme_dir),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "NODE_ENV": "production"},
        )

        if build_result.returncode != 0:
            raise ValueError(f"Build failed: {build_result.stderr.strip()[:500]}")

        # Find the output bundle
        dist_dir = theme_dir / "dist"
        bundle_path = None
        for name in ["theme.js", "theme.mjs", "theme.esm.js"]:
            candidate = dist_dir / name
            if candidate.exists():
                bundle_path = candidate
                break

        if not bundle_path:
            raise ValueError(
                "Build produced no output. Expected dist/theme.js or dist/theme.mjs"
            )

        # Check bundle size
        bundle_size = bundle_path.stat().st_size
        if bundle_size > MAX_BUNDLE_SIZE:
            raise ValueError(
                f"Bundle too large: {bundle_size / 1024 / 1024:.1f}MB "
                f"(max {MAX_BUNDLE_SIZE / 1024 / 1024:.0f}MB)"
            )

        # ── Step 5: Security scan ────────────────────────────────────────
        violations = _security_scan_bundle(bundle_path)
        if violations:
            raise ValueError(f"Security scan failed: {'; '.join(violations)}")

        # ── Step 5.5: Theme-contract gate ────────────────────────────────
        # Validate the emitted dist/manifest.json + dist/import-map.json
        # against the platform contract (manifest fields, preset→section
        # coverage, contract-version compat). Defends against a bundle built
        # by a bypassed/old plugin even though the plugin also self-checks.
        from src.core.theme_contract import validate_dist_bundle

        contract_errors = validate_dist_bundle(dist_dir)
        if contract_errors:
            raise ValueError(
                "Theme contract validation failed: " + "; ".join(contract_errors)
            )

        # ── Step 6: Upload to CDN ────────────────────────────────────────
        _update_build_status(build_id, status="uploading")
        logger.info("Uploading theme bundle to CDN")

        # Use the existing storage service to upload
        from src.infrastructure.external_services.cloudflare_r2.storage_service import (
            CloudflareR2StorageService,
        )

        storage = CloudflareR2StorageService()

        import asyncio

        loop = asyncio.new_event_loop()

        # Upload JS bundle
        bundle_content = bundle_path.read_bytes()
        bundle_uploaded = loop.run_until_complete(
            storage.upload_file(
                file_content=bundle_content,
                filename=f"{theme_id}-{build_id[:8]}.js",
                content_type="application/javascript",
                bucket="themes",
            )
        )
        bundle_url = bundle_uploaded.url

        # Upload CSS if exists
        css_path = dist_dir / "theme.css"
        css_url = None
        if css_path.exists():
            css_content = css_path.read_bytes()
            css_uploaded = loop.run_until_complete(
                storage.upload_file(
                    file_content=css_content,
                    filename=f"{theme_id}-{build_id[:8]}.css",
                    content_type="text/css",
                    bucket="themes",
                )
            )
            css_url = css_uploaded.url

        loop.close()

        # ── Step 7: Read settings_schema from bundle ─────────────────────
        # Used by the merchant dashboard to render the customizer UI
        settings_schema = None
        schema_path = theme_dir / "settings_schema.json"
        if schema_path.exists():
            try:
                settings_schema = json.loads(schema_path.read_text(encoding="utf-8"))
                logger.info(
                    "Read settings_schema with %d settings",
                    len(settings_schema.get("settings", [])),
                )
            except Exception as e:
                logger.warning("Failed to parse settings_schema.json: %s", e)

        # Optional: read sections.json (manifest of section types this bundle
        # ships). Mirrors the dev-mode connect endpoint so production-built
        # external themes also surface their custom sections in the dashboard
        # picker. Bundles that don't ship sections won't have the file —
        # that's fine, the dashboard handles a missing manifest gracefully.
        sections_manifest = None
        sections_path = theme_dir / "sections.json"
        if sections_path.exists():
            try:
                sections_manifest = json.loads(
                    sections_path.read_text(encoding="utf-8")
                )
                logger.info(
                    "Read sections.json with %d sections",
                    len(sections_manifest.get("sections", [])),
                )
            except Exception as e:
                logger.warning("Failed to parse sections.json: %s", e)

        # ── Phase 7.3: upload static error + loading templates ───────────
        # The theme's `theme.json` declares `error_template` /
        # `loading_template` relative paths (conventionally
        # `templates/error.html` and `templates/loading.html`). The
        # storefront fetches the URLs we store here and injects the
        # HTML on the client-side error boundary / streaming loading
        # skeleton — neither has access to the React tree at render
        # time, which is why these are static HTML files instead of
        # SDK components.
        loop3 = asyncio.new_event_loop()

        def _upload_static_template(rel_path: str | None, suffix: str) -> str | None:
            if not rel_path:
                return None
            tpl_path = theme_dir / rel_path
            if not tpl_path.exists():
                return None
            try:
                content = tpl_path.read_bytes()
                uploaded = loop3.run_until_complete(
                    storage.upload_file(
                        file_content=content,
                        filename=f"{theme_id}-{build_id[:8]}-{suffix}.html",
                        content_type="text/html; charset=utf-8",
                        bucket="themes",
                    )
                )
                return uploaded.url
            except Exception as e:
                logger.warning("Failed to upload static template %s: %s", rel_path, e)
                return None

        error_template_url = _upload_static_template(
            manifest.get("error_template"), "error"
        )
        loading_template_url = _upload_static_template(
            manifest.get("loading_template"), "loading"
        )
        loop3.close()

        # ── Step 8: Update store theme_settings ──────────────────────────
        logger.info("Updating store theme_settings with CDN URLs")

        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.repositories.store_repository import (
            SQLAlchemyStoreRepository,
        )

        async def _update_store():
            async with AsyncSessionLocal() as session:
                repo = SQLAlchemyStoreRepository(session)
                store = await repo.get_by_id(store_id)
                if not store:
                    raise ValueError(f"Store {store_id} not found")

                # theme_settings is a TOP-LEVEL JSONB column, not nested in settings
                theme_settings = dict(store.theme_settings or {})

                # Preserve any merchant_settings the merchant has already
                # edited against this same theme — a rebuild shouldn't wipe
                # their customizations. Only carry them across if the rebuild
                # is for the SAME theme_id (a different theme would have a
                # different settings schema, so the values wouldn't apply).
                existing_external = theme_settings.get("external_theme") or {}
                preserved_merchant_settings = (
                    existing_external.get("merchant_settings")
                    if existing_external.get("theme_id") == theme_id
                    else None
                )

                # Set the external_theme config (with manifest + schema for the dashboard)
                theme_settings["external_theme"] = {
                    "bundle_url": bundle_url,
                    "css_url": css_url,
                    "theme_id": theme_id,
                    "name": manifest.get("name", theme_id),
                    "nameAr": manifest.get("nameAr", theme_id),
                    "description": manifest.get("description", ""),
                    "version": manifest.get("version", "1.0.0"),
                    "author": manifest.get("author", "Unknown"),
                    "tags": manifest.get("tags", []),
                    "source_repo": github_url or "code-editor",
                    "built_at": datetime.now(UTC).isoformat(),
                    "settings_schema": settings_schema,
                    # Section schemas extracted from the bundle's sections.json,
                    # consumed by the dashboard's section picker. None when the
                    # bundle ships no custom sections.
                    "section_schemas": sections_manifest,
                    # Phase 7.3 — static BYOT templates. None when the theme
                    # didn't declare them; storefront falls back to platform
                    # chrome gracefully.
                    "error_template_url": error_template_url,
                    "loading_template_url": loading_template_url,
                }
                if preserved_merchant_settings is not None:
                    theme_settings["external_theme"]["merchant_settings"] = (
                        preserved_merchant_settings
                    )

                # Set base_theme to the external theme's ID — this makes it
                # the active theme on the storefront immediately
                if "theme" not in theme_settings:
                    theme_settings["theme"] = {}
                theme_settings["theme"]["base_theme"] = theme_id

                await repo.update(store_id, {"theme_settings": theme_settings})
                await session.commit()

        loop2 = asyncio.new_event_loop()
        loop2.run_until_complete(_update_store())
        loop2.close()

        # ── Done ─────────────────────────────────────────────────────────
        _update_build_status(
            build_id,
            status="complete",
            bundle_url=bundle_url,
            css_url=css_url,
            theme_id=theme_id,
            completed_at=datetime.now(UTC),
        )

        logger.info(
            "Theme build complete: %s → %s",
            theme_id,
            bundle_url,
        )

        return {
            "build_id": build_id,
            "status": "complete",
            "theme_id": theme_id,
            "bundle_url": bundle_url,
            "css_url": css_url,
        }

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error("Theme build failed: %s", error_msg)

        _update_build_status(
            build_id,
            status="failed",
            error=error_msg,
            completed_at=datetime.now(UTC),
        )

        return {
            "build_id": build_id,
            "status": "failed",
            "error": error_msg,
        }

    finally:
        # Cleanup temp directory
        if work_dir and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
