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
) -> dict:
    """Build an external theme from a GitHub repository.

    Steps:
    1. Shallow clone the repo
    2. Validate theme contract (theme.json, settings_schema.json, etc.)
    3. npm install (with --ignore-scripts for security)
    4. npm run build (expects numu-theme-plugin in vite.config)
    5. Security-scan the output bundle
    6. Upload to R2/S3
    7. Update store's theme_settings
    """
    work_dir = None

    try:
        # ── Step 1: Clone ────────────────────────────────────────────────
        _update_build_status(build_id, status="cloning")
        logger.info("Cloning theme repo: %s (branch: %s)", github_url, branch)

        work_dir = Path(tempfile.mkdtemp(prefix="numu-theme-"))
        clone_result = subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                f"--branch={branch}",
                github_url,
                str(work_dir / "theme"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if clone_result.returncode != 0:
            raise ValueError(f"Git clone failed: {clone_result.stderr.strip()}")

        theme_dir = work_dir / "theme"

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

        # ── Step 8: Update store theme_settings ──────────────────────────
        logger.info("Updating store theme_settings with CDN URLs")

        from src.infrastructure.database.session import get_session
        from src.infrastructure.repositories.store_repository import (
            SQLAlchemyStoreRepository,
        )

        async def _update_store():
            async with get_session() as session:
                repo = SQLAlchemyStoreRepository(session)
                store = await repo.get_by_id(store_id)
                if not store:
                    raise ValueError(f"Store {store_id} not found")

                # theme_settings is a TOP-LEVEL JSONB column, not nested in settings
                theme_settings = dict(store.theme_settings or {})

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
                    "source_repo": github_url,
                    "built_at": datetime.now(UTC).isoformat(),
                    "settings_schema": settings_schema,
                }

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
