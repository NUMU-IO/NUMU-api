"""Theme ZIP upload + build pipeline.

A more security-hardened evolution of theme_build_tasks.py that:

1. Accepts a ZIP file instead of a GitHub URL (no network access needed
   for source fetching — a tighter attack surface)
2. Runs the build inside an ephemeral Docker container with network
   restrictions and resource limits (see _run_in_docker)
3. Replaces regex-based security scanning with AST scanning via Node/acorn
4. Writes the result into the new themes + theme_versions tables (Phase 1)
   instead of mutating stores.theme_settings directly

Usage:

    from src.infrastructure.messaging.tasks.theme_upload_tasks import build_theme_from_zip
    build_theme_from_zip.delay(build_id=..., zip_path=..., uploader_id=...)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

MAX_ZIP_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_BUNDLE_SIZE = 2 * 1024 * 1024  # 2 MB
MAX_EXTRACTED_SIZE = 50 * 1024 * 1024  # 50 MB (zip bomb protection)
DOCKER_IMAGE = os.getenv("NUMU_THEME_BUILDER_IMAGE", "numu-theme-builder:latest")
USE_DOCKER = os.getenv("NUMU_THEME_USE_DOCKER", "false").lower() == "true"


# ── Exceptions ────────────────────────────────────────────────────────────────


class ThemeBuildError(Exception):
    """Raised when theme build fails."""


# ── ZIP handling ──────────────────────────────────────────────────────────────


def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    """Extract a ZIP archive safely, rejecting zip bombs and path traversal.

    Security measures:
    - Reject if total uncompressed size exceeds MAX_EXTRACTED_SIZE
    - Reject paths that escape the destination directory
    - Reject symlinks and hard links
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        total_size = 0
        for info in zf.infolist():
            # Path traversal protection
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ThemeBuildError(f"Unsafe zip entry path: {info.filename}")

            # Reject symlinks (external attribute bits on Unix)
            is_symlink = (info.external_attr >> 28) == 0xA
            if is_symlink:
                raise ThemeBuildError(f"Symlink not allowed: {info.filename}")

            total_size += info.file_size
            if total_size > MAX_EXTRACTED_SIZE:
                raise ThemeBuildError(
                    f"Extracted size exceeds {MAX_EXTRACTED_SIZE // 1024 // 1024}MB limit"
                )

        zf.extractall(dest)


# ── AST-based security scanning ───────────────────────────────────────────────


# JavaScript AST scanner run inside Node.js. Returns JSON list of violations.
# Uses the `acorn` AST parser (lightweight, no deps beyond Node).
#
# Detects:
# - CallExpression with callee "eval" or "Function"
# - MemberExpression accessing document.cookie / document.write
# - ImportDeclaration importing fs, child_process, net, http, https
# - Assignment to innerHTML (common XSS vector)
# - window.open with external URLs (phishing)
AST_SCANNER_JS = r"""
const fs = require('fs');
const path = require('path');
const acornPath = require.resolve('acorn');
const acorn = require(acornPath);

const src = fs.readFileSync(process.argv[2], 'utf8');
const violations = [];

let ast;
try {
  ast = acorn.parse(src, {
    ecmaVersion: 2023,
    sourceType: 'module',
    allowImportExportEverywhere: true,
    allowHashBang: true,
  });
} catch (e) {
  violations.push({ kind: 'parse_error', message: e.message });
  console.log(JSON.stringify({ violations }));
  process.exit(0);
}

const BANNED_NODE_MODULES = new Set([
  'fs', 'child_process', 'net', 'http', 'https', 'dgram',
  'dns', 'cluster', 'os', 'tls', 'v8', 'vm'
]);

function walk(node, parent) {
  if (!node || typeof node !== 'object') return;

  // eval / Function constructor
  if (node.type === 'CallExpression' && node.callee) {
    if (node.callee.type === 'Identifier') {
      if (node.callee.name === 'eval') {
        violations.push({ kind: 'eval', message: 'eval() is not allowed' });
      }
      if (node.callee.name === 'Function') {
        violations.push({ kind: 'function_ctor', message: 'Function constructor not allowed' });
      }
    }
  }

  // new Function(...)
  if (node.type === 'NewExpression' && node.callee && node.callee.type === 'Identifier') {
    if (node.callee.name === 'Function') {
      violations.push({ kind: 'function_ctor', message: 'new Function() not allowed' });
    }
  }

  // document.cookie, document.write, document.writeln
  if (node.type === 'MemberExpression') {
    const obj = node.object;
    const prop = node.property;
    if (obj && obj.type === 'Identifier' && obj.name === 'document' &&
        prop && prop.type === 'Identifier') {
      if (['cookie', 'write', 'writeln', 'domain'].includes(prop.name)) {
        violations.push({
          kind: 'document_access',
          message: `document.${prop.name} is not allowed`,
        });
      }
    }
  }

  // innerHTML = ... assignment
  if (node.type === 'AssignmentExpression' && node.left &&
      node.left.type === 'MemberExpression' && node.left.property &&
      node.left.property.type === 'Identifier') {
    if (['innerHTML', 'outerHTML'].includes(node.left.property.name)) {
      violations.push({
        kind: 'innerhtml_assignment',
        message: `${node.left.property.name} assignment is a security risk`,
      });
    }
  }

  // import / require of banned node modules
  if (node.type === 'ImportDeclaration' && node.source) {
    const src = node.source.value;
    if (typeof src === 'string' && BANNED_NODE_MODULES.has(src)) {
      violations.push({ kind: 'banned_import', message: `Import of "${src}" not allowed` });
    }
  }
  if (node.type === 'CallExpression' && node.callee &&
      node.callee.type === 'Identifier' && node.callee.name === 'require' &&
      node.arguments && node.arguments[0] && node.arguments[0].type === 'Literal') {
    const modName = node.arguments[0].value;
    if (BANNED_NODE_MODULES.has(modName)) {
      violations.push({ kind: 'banned_require', message: `require("${modName}") not allowed` });
    }
  }

  // Recurse
  for (const key of Object.keys(node)) {
    if (key === 'type' || key === 'loc' || key === 'start' || key === 'end') continue;
    const child = node[key];
    if (Array.isArray(child)) {
      for (const c of child) walk(c, node);
    } else if (child && typeof child === 'object') {
      walk(child, node);
    }
  }
}

walk(ast, null);
console.log(JSON.stringify({ violations }));
"""


def _ast_security_scan(bundle_path: Path) -> list[str]:
    """Run the AST security scanner against a built bundle.

    Returns a list of human-readable violation messages. Empty list = safe.
    If Node.js or acorn is not available, falls back to a basic regex scan
    and logs a warning (better than no scan at all).
    """
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(AST_SCANNER_JS)
            scanner_path = f.name

        result = subprocess.run(
            ["node", scanner_path, str(bundle_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        os.unlink(scanner_path)

        if result.returncode != 0:
            logger.warning("AST scanner error: %s", result.stderr)
            return _fallback_regex_scan(bundle_path)

        data = json.loads(result.stdout)
        violations = data.get("violations", [])
        return [f"{v['kind']}: {v['message']}" for v in violations]
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        logger.warning("AST scan failed (%s); falling back to regex scan", e)
        return _fallback_regex_scan(bundle_path)


def _fallback_regex_scan(bundle_path: Path) -> list[str]:
    """Basic regex-based security scan (last-resort fallback)."""
    dangerous = [
        ("eval(", "eval() is dangerous"),
        ("new Function(", "Function constructor is dangerous"),
        ("document.cookie", "document.cookie access not allowed"),
        ("document.write(", "document.write() not allowed"),
        (".innerHTML", "innerHTML assignment is a security risk"),
    ]
    content = bundle_path.read_text(encoding="utf-8", errors="replace")
    return [msg for pattern, msg in dangerous if pattern in content]


# ── Docker-based isolated build ───────────────────────────────────────────────


def _run_in_docker(theme_dir: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run the theme build inside a restricted Docker container.

    The container:
    - Has no network except the npm registry (via a whitelist proxy, or
      offline via pre-populated node_modules)
    - Has 512MB memory + 1 CPU limit
    - Mounts the theme source read-only at /theme-src
    - Writes dist output to a writable volume at /theme-dist
    - Runs as an unprivileged user

    The Docker image `numu-theme-builder` is built separately (Dockerfile
    shipped under docker/theme-builder/).
    """
    # The "/tmp:size=100M" string here is a Docker --tmpfs flag value referring
    # to the path INSIDE the ephemeral container (not a host filesystem path),
    # so the bandit B108 hardcoded-tmp-directory rule does not apply.
    container_tmpfs = "/tmp:size=100M"  # nosec B108
    return subprocess.run(  # nosec B603 B607
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--tmpfs",
            container_tmpfs,
            "--memory=512m",
            "--cpus=1.0",
            "--network=none",
            "--user=1000:1000",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-v",
            f"{theme_dir}:/theme-src:ro",
            "-v",
            f"{theme_dir / 'dist'}:/theme-dist",
            DOCKER_IMAGE,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_local_build(
    theme_dir: Path, timeout: int = 300
) -> subprocess.CompletedProcess:
    """Fallback: run the build directly (used when Docker is not available).

    SECURITY WARNING: This runs untrusted code on the worker host. Always
    prefer Docker isolation in production.
    """
    logger.warning(
        "Running theme build WITHOUT Docker isolation. "
        "Set NUMU_THEME_USE_DOCKER=true in production."
    )
    # Install
    subprocess.run(
        ["npm", "install", "--ignore-scripts", "--no-audit"],
        cwd=str(theme_dir),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    # Build
    return subprocess.run(
        ["npm", "run", "build"],
        cwd=str(theme_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "NODE_ENV": "production"},
    )


# ── Theme contract validation ─────────────────────────────────────────────────


def _validate_theme_contract(theme_dir: Path) -> dict:
    """Validate that the uploaded theme meets the NUMU theme contract.

    Required files:
    - theme.json (id, name, version, layout)
    - settings_schema.json
    - styles.css
    - index.ts / index.tsx / numu.config.ts (entry point)

    Returns the parsed theme.json manifest.
    """
    required = ["theme.json", "settings_schema.json", "styles.css"]
    for f in required:
        if not (theme_dir / f).exists():
            raise ThemeBuildError(f"Missing required file: {f}")

    has_entry = any(
        (theme_dir / name).exists()
        for name in ["index.ts", "index.tsx", "numu.config.ts", "numu.config.tsx"]
    )
    if not has_entry:
        raise ThemeBuildError(
            "Missing entry point: need index.ts, index.tsx, or numu.config.ts"
        )

    with open(theme_dir / "theme.json") as f:
        manifest = json.load(f)

    required_fields = ["id", "name", "version", "layout"]
    missing = [fld for fld in required_fields if fld not in manifest]
    if missing:
        raise ThemeBuildError(
            f"theme.json missing required fields: {', '.join(missing)}"
        )

    # Validate semver version
    version = manifest["version"]
    if not isinstance(version, str):
        raise ThemeBuildError("theme.json: 'version' must be a string")
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ThemeBuildError(
            f"theme.json: 'version' must be semver (x.y.z), got '{version}'"
        )

    # Validate id format
    theme_id = manifest["id"]
    if not isinstance(theme_id, str) or not theme_id.strip():
        raise ThemeBuildError("theme.json: 'id' must be a non-empty string")
    if not all(c.isalnum() or c in "-_" for c in theme_id):
        raise ThemeBuildError(
            f"theme.json: 'id' must contain only alphanumerics, - or _, got '{theme_id}'"
        )

    return manifest


# ── Status tracking (Redis-backed, with in-memory fallback) ────────────────────


_build_statuses: dict[str, dict] = {}


def _update_status(build_id: str, **kwargs) -> None:
    """Update build status. TODO: back with Redis in production."""
    if build_id not in _build_statuses:
        _build_statuses[build_id] = {"build_id": build_id}
    _build_statuses[build_id].update(kwargs)
    _build_statuses[build_id]["updated_at"] = datetime.now(UTC).isoformat()


def get_build_status(build_id: str) -> dict | None:
    return _build_statuses.get(build_id)


# ── Main Celery task ──────────────────────────────────────────────────────────


@celery_app.task(
    name="build_theme_from_zip",
    bind=True,
    max_retries=0,  # No retries — user can re-upload
    soft_time_limit=300,
    time_limit=360,
)
def build_theme_from_zip(
    self,
    build_id: str,
    zip_path: str,
    uploader_id: str | None = None,
) -> dict:
    """Build a theme from an uploaded ZIP and register it in themes/theme_versions.

    Steps:
    1. Extract ZIP safely (zip bomb / path traversal protection)
    2. Validate theme contract (required files + theme.json)
    3. Build (Docker-isolated if NUMU_THEME_USE_DOCKER=true)
    4. Size check + AST security scan
    5. Upload bundle + CSS to R2
    6. Upsert themes + theme_versions rows
    """
    work_dir: Path | None = None
    try:
        _update_status(build_id, status="extracting")
        logger.info("Extracting ZIP %s for build %s", zip_path, build_id)

        work_dir = Path(tempfile.mkdtemp(prefix="numu-theme-zip-"))
        theme_dir = work_dir / "theme"
        theme_dir.mkdir(parents=True)
        _safe_extract_zip(Path(zip_path), theme_dir)

        # ── Validate contract ───────────────────────────────────────────────
        _update_status(build_id, status="validating")
        manifest = _validate_theme_contract(theme_dir)
        theme_id_slug = manifest["id"]
        version = manifest["version"]
        _update_status(build_id, theme_slug=theme_id_slug, version=version)

        # ── Build ───────────────────────────────────────────────────────────
        _update_status(build_id, status="building")
        dist = theme_dir / "dist"
        dist.mkdir(exist_ok=True)

        if USE_DOCKER:
            result = _run_in_docker(theme_dir)
        else:
            result = _run_local_build(theme_dir)

        if result.returncode != 0:
            raise ThemeBuildError(f"Build failed: {result.stderr.strip()[:500]}")

        bundle_path = None
        for name in ["theme.js", "theme.mjs", "theme.esm.js"]:
            candidate = dist / name
            if candidate.exists():
                bundle_path = candidate
                break
        if not bundle_path:
            raise ThemeBuildError("Build produced no bundle (dist/theme.js)")

        # ── Size check ──────────────────────────────────────────────────────
        size = bundle_path.stat().st_size
        if size > MAX_BUNDLE_SIZE:
            raise ThemeBuildError(
                f"Bundle too large: {size / 1024 / 1024:.1f}MB "
                f"(max {MAX_BUNDLE_SIZE // 1024 // 1024}MB)"
            )

        # ── Security scan ───────────────────────────────────────────────────
        _update_status(build_id, status="scanning")
        violations = _ast_security_scan(bundle_path)
        if violations:
            raise ThemeBuildError(f"Security scan failed: {'; '.join(violations[:5])}")

        # ── Compute checksum ────────────────────────────────────────────────
        bundle_bytes = bundle_path.read_bytes()
        checksum = hashlib.sha256(bundle_bytes).hexdigest()
        version_hash = checksum[:8]

        # ── Upload to R2 ────────────────────────────────────────────────────
        _update_status(build_id, status="uploading")

        from src.infrastructure.external_services.cloudflare_r2.storage_service import (
            CloudflareR2StorageService,
        )

        storage = CloudflareR2StorageService()
        import asyncio

        loop = asyncio.new_event_loop()

        bundle_key = f"themes/{theme_id_slug}/{version}-{version_hash}/theme.js"
        bundle_uploaded = loop.run_until_complete(
            storage.upload_file(
                file_content=bundle_bytes,
                filename=bundle_key,
                content_type="application/javascript",
                bucket="themes",
            )
        )
        bundle_url = bundle_uploaded.url

        css_url = None
        css_path = dist / "theme.css"
        if css_path.exists():
            css_key = f"themes/{theme_id_slug}/{version}-{version_hash}/theme.css"
            css_uploaded = loop.run_until_complete(
                storage.upload_file(
                    file_content=css_path.read_bytes(),
                    filename=css_key,
                    content_type="text/css",
                    bucket="themes",
                )
            )
            css_url = css_uploaded.url

        loop.close()

        # ── Upsert themes + theme_versions rows ─────────────────────────────
        _update_status(build_id, status="registering")

        settings_schema = {}
        schema_file = theme_dir / "settings_schema.json"
        if schema_file.exists():
            try:
                settings_schema = json.loads(schema_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Bad settings_schema.json: %s", e)

        section_schemas = None
        sections_file = theme_dir / "sections.json"
        if sections_file.exists():
            try:
                section_schemas = json.loads(sections_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Bad sections.json: %s", e)

        async def _register():
            from src.core.entities.theme import (
                Theme,
                ThemeStatus,
                ThemeType,
                ThemeVersion,
            )
            from src.infrastructure.database.session import get_session
            from src.infrastructure.repositories.theme_repository import ThemeRepository
            from src.infrastructure.repositories.theme_version_repository import (
                ThemeVersionRepository,
            )

            async with get_session() as session:
                theme_repo = ThemeRepository(session)
                version_repo = ThemeVersionRepository(session)

                existing = await theme_repo.get_by_slug(theme_id_slug)
                if existing:
                    theme = existing
                else:
                    theme = Theme(
                        id=uuid4(),
                        name=manifest.get("name", theme_id_slug),
                        slug=theme_id_slug,
                        description=manifest.get("description"),
                        author=manifest.get("author", "Community"),
                        type=ThemeType.EXTERNAL,
                        status=ThemeStatus.PUBLISHED,
                        is_public=False,  # Private until reviewed
                        settings_schema=settings_schema,
                        section_schemas=section_schemas,
                        supported_features=manifest.get("supports"),
                        created_by=uuid4() if uploader_id is None else uuid4(),
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    theme = await theme_repo.create(theme)

                theme_version = ThemeVersion(
                    id=uuid4(),
                    theme_id=theme.id,
                    version=version,
                    bundle_url=bundle_url,
                    css_url=css_url,
                    manifest=manifest,
                    changelog=manifest.get("changelog"),
                    is_latest=True,
                    size_bytes=size,
                    checksum=checksum,
                    published_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                theme_version = await version_repo.create(theme_version)

                return {
                    "theme_id": str(theme.id),
                    "version_id": str(theme_version.id),
                }

        loop = asyncio.new_event_loop()
        ids = loop.run_until_complete(_register())
        loop.close()

        _update_status(
            build_id,
            status="complete",
            theme_id=ids["theme_id"],
            version_id=ids["version_id"],
            bundle_url=bundle_url,
            css_url=css_url,
            checksum=checksum,
            size_bytes=size,
        )
        logger.info(
            "Theme %s v%s built successfully: %s", theme_id_slug, version, bundle_url
        )
        return _build_statuses[build_id]

    except ThemeBuildError as e:
        logger.error("Build %s failed: %s", build_id, e)
        _update_status(build_id, status="failed", error=str(e))
        return _build_statuses[build_id]
    except Exception as e:
        logger.exception("Build %s crashed", build_id)
        _update_status(build_id, status="failed", error=f"Internal error: {e}")
        raise
    finally:
        if work_dir and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        # Delete the uploaded ZIP
        try:
            os.unlink(zip_path)
        except OSError:
            pass
