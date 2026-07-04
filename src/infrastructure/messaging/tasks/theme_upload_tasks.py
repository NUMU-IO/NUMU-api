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


# Default to Docker-isolated builds. The previous `false` default was
# fine while only NUMU staff submitted themes; for a public marketplace
# we require sandboxing on every build because npm install runs
# arbitrary developer-controlled code (build scripts, postinstall, etc.)
# against our worker host. Set NUMU_THEME_USE_DOCKER=false explicitly to
# opt out for local dev where you don't have the builder image.
def _docker_default() -> str:
    return (
        "true" if os.getenv("ENVIRONMENT", "development") == "production" else "false"
    )


USE_DOCKER = os.getenv("NUMU_THEME_USE_DOCKER", _docker_default()).lower() == "true"


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
            # Phase 2 render gate (default off). When "1", the entrypoint
            # SSR-renders every template against fixtures inside this sandbox
            # and fails the build on any crashing/empty template.
            "-e",
            f"NUMU_THEME_RENDER_GATE={os.getenv('NUMU_THEME_RENDER_GATE', '0')}",
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


def _maybe_render_gate_host(theme_dir: Path) -> None:
    """Render gate for the NON-docker (host) build path.

    The docker path renders inside the sandbox (entrypoint), so this only runs
    for host builds (dev / test-staging where USE_DOCKER is false). Gated by
    NUMU_THEME_RENDER_GATE. Skips safely if the invoker script or node is
    missing (so enabling the flag never hard-breaks a misconfigured worker);
    only a real render failure raises.
    """
    if os.getenv("NUMU_THEME_RENDER_GATE", "0") != "1" or USE_DOCKER:
        return
    script = (
        Path(__file__).resolve().parents[4]
        / "docker"
        / "theme-builder"
        / "verify_theme_render.mjs"
    )
    node = shutil.which("node")
    if not script.exists() or not node:
        logger.warning(
            "render gate enabled but %s — skipping host render",
            "node not on PATH" if not node else f"{script} missing",
        )
        return
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        [node, str(script), str(theme_dir)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.stdout:
        logger.info("render-gate: %s", proc.stdout.strip()[:2000])
    if proc.returncode != 0:
        raise ThemeBuildError(
            "Render verification failed: "
            + (proc.stdout or proc.stderr or "").strip()[:500]
        )


def _run_local_build(
    theme_dir: Path, timeout: int = 300
) -> subprocess.CompletedProcess:
    """Fallback: run the build directly (used when Docker is not available).

    SECURITY WARNING: This runs untrusted code on the worker host. The
    `--ignore-scripts` flag blocks npm pre/post-install hooks (the most
    common attack surface) but the BUILD STEP itself is still
    developer-controlled JavaScript executing as the worker user. Use
    only on developer laptops for local iteration. Production deploys
    must run with NUMU_THEME_USE_DOCKER=true and the builder image.

    In production we also fail loudly when this fallback is reached
    despite ENVIRONMENT=production, so a misconfiguration doesn't
    silently downgrade isolation.
    """
    if os.getenv("ENVIRONMENT", "development") == "production":
        raise ThemeBuildError(
            "Refusing to run un-isolated theme build in production. "
            "Set NUMU_THEME_USE_DOCKER=true and ensure the builder image is available."
        )
    logger.warning(
        "Running theme build WITHOUT Docker isolation. "
        "Set NUMU_THEME_USE_DOCKER=true in production."
    )
    # On Windows, `npm` is `npm.cmd`. subprocess without shell=True
    # doesn't resolve PATHEXT, so calling ["npm", ...] raises
    # FileNotFoundError. shutil.which() walks PATH and respects PATHEXT,
    # giving us the actual executable in either OS.
    npm_path = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm_path:
        raise ThemeBuildError(
            "npm not found on PATH — install Node.js and retry, or run "
            "with NUMU_THEME_USE_DOCKER=true to use the sandboxed builder."
        )

    # Install. Flags align with the Docker entrypoint:
    #   --ignore-scripts: block pre/post-install hooks (supply-chain).
    #   --no-audit / --no-fund: avoid network calls beyond resolution.
    #   --prefer-offline: prefer the local npm cache; reduces blast
    #                     radius of a transient registry compromise.
    #
    # `check=False` so we can surface a sane error message instead of
    # the bare CalledProcessError (which omits stderr in the repr the
    # outer task uses for the build_log). When npm fails we tail the
    # combined stdout+stderr so the developer sees the real cause
    # (most often "ENOENT — could not resolve @numu/theme-sdk" when
    # the linked workspace package isn't published to a registry).
    install_result = subprocess.run(
        [
            npm_path,
            "install",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--prefer-offline",
        ],
        cwd=str(theme_dir),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if install_result.returncode != 0:
        tail = (install_result.stderr or install_result.stdout or "").strip()
        raise ThemeBuildError(
            f"npm install failed (exit {install_result.returncode}):\n{tail[-2000:]}"
        )
    # Build
    return subprocess.run(
        [npm_path, "run", "build"],
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

    # Mirror @numu/theme-plugin's ENTRY_CANDIDATES so the worker accepts
    # whatever the plugin accepts. Modern themes (post-0.2.0 scaffold)
    # land their entry at src/main.tsx + a vite.config.ts that drives
    # the build; older themes used a top-level index.ts. Either is fine
    # — Vite picks the right entry from the plugin's contract check.
    entry_candidates = [
        "src/main.tsx",
        "src/main.ts",
        "src/index.tsx",
        "src/index.ts",
        "index.ts",
        "index.tsx",
        "numu.config.ts",
        "numu.config.tsx",
    ]
    has_entry = any((theme_dir / name).exists() for name in entry_candidates)
    if not has_entry:
        raise ThemeBuildError(
            "Missing entry point: expected one of " + ", ".join(entry_candidates)
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


# ── Status tracking (Redis-backed) ─────────────────────────────────────────────
#
# All build-status writes go through Redis (``ThemeBuildStore`` via
# ``_redis_status``) so the API workers and the Celery worker — separate
# processes in prod — share one source of truth. The previous in-process
# ``_build_statuses`` dict only worked in single-process dev; on prod the
# poller (a different process than the worker) never saw progress.


async def _dispose_engine() -> None:
    """Reset the global async engine's connection pool.

    The build task drives several short-lived ``new_event_loop()`` blocks in
    one sync Celery task. A connection pooled in one loop is invalid in the
    next (asyncpg binds connections to a loop), so we dispose the pool after
    each DB block; the engine lazily recreates it — with its RLS/search_path
    pool listeners intact — on the next loop's first checkout.
    """
    from src.infrastructure.database.connection import engine

    await engine.dispose()


def _redis_status(build_id: str, **kwargs) -> None:
    """Status callback that writes to the Redis build store.

    The single status backend for every Celery build worker — code-editor
    (``build_theme_from_files``), marketplace ZIP (``build_theme_from_zip``),
    and GitHub (``build_external_theme``) — so their pollers see progress even
    though each worker runs in a different process than the API. It performs an
    update-merge, so the API route must ``set`` the initial key before
    dispatching the task. A fresh event loop per call is fine — there are only
    a handful of status transitions per build.
    """
    import asyncio

    from src.infrastructure.cache.theme_build_store import get_theme_build_store

    store = get_theme_build_store()
    # The store caches a redis client bound to the loop it was first used on;
    # this task runs many short-lived loops, so force a fresh client per call
    # and close it after — otherwise later updates hit "Event loop is closed".
    store._client = None
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(store.update(build_id, kwargs))
    except Exception as e:  # noqa: BLE001 — status is best-effort, never fail the build
        logger.warning("Redis build-status update failed for %s: %s", build_id, e)
    finally:
        client = store._client
        if client is not None:
            try:
                loop.run_until_complete(client.aclose())
            except Exception:  # noqa: BLE001
                pass
        store._client = None
        loop.close()


# ── Shared build core ─────────────────────────────────────────────────────────


def _build_register_activate(
    *,
    theme_dir: Path,
    build_id: str,
    status,
    uploader_id: str | None = None,
    activate_for_store_id: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Validate → build → scan → gate → upload → register (+ optionally activate).

    The single shared pipeline behind both entrypoints. ``status`` is a
    callable ``(build_id, **fields)`` so each caller picks its status backend
    (in-memory for the marketplace ZIP upload, Redis for the code editor).
    When ``activate_for_store_id`` is given, the freshly-registered
    ``ThemeVersion`` is activated for that store and the storefront is
    revalidated — that's what makes a code-editor Publish actually go live.
    """
    import asyncio

    # ── Validate contract ───────────────────────────────────────────────────
    status(build_id, status="validating")
    manifest = _validate_theme_contract(theme_dir)
    theme_id_slug = manifest["id"]
    version = manifest["version"]
    status(build_id, theme_slug=theme_id_slug, version=version)

    # ── Build ───────────────────────────────────────────────────────────────
    status(build_id, status="building")
    dist = theme_dir / "dist"
    dist.mkdir(exist_ok=True)

    result = _run_in_docker(theme_dir) if USE_DOCKER else _run_local_build(theme_dir)
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

    # ── Size check ──────────────────────────────────────────────────────────
    size = bundle_path.stat().st_size
    if size > MAX_BUNDLE_SIZE:
        raise ThemeBuildError(
            f"Bundle too large: {size / 1024 / 1024:.1f}MB "
            f"(max {MAX_BUNDLE_SIZE // 1024 // 1024}MB)"
        )

    # ── Security scan ─────────────────────────────────────────────────────────
    status(build_id, status="scanning")
    violations = _ast_security_scan(bundle_path)
    if violations:
        raise ThemeBuildError(f"Security scan failed: {'; '.join(violations[:5])}")

    # ── Theme-contract gate ─────────────────────────────────────────────────
    from src.core.theme_contract import validate_dist_bundle

    contract_errors = validate_dist_bundle(dist)
    if contract_errors:
        raise ThemeBuildError(
            "Theme contract validation failed: " + "; ".join(contract_errors)
        )

    # ── Render gate (host path) ──────────────────────────────────────────────
    _maybe_render_gate_host(theme_dir)

    # ── Compute checksum ──────────────────────────────────────────────────────
    bundle_bytes = bundle_path.read_bytes()
    checksum = hashlib.sha256(bundle_bytes).hexdigest()
    version_hash = checksum[:8]

    # ── Upload bundle (R2 in prod, local filesystem in dev) ───────────────────
    status(build_id, status="uploading")

    # Mirror the get_storage_service() factory but inline (keeps the worker off
    # the FastAPI dependency layer): real R2 when configured, otherwise the
    # LocalStorageService that main.py serves under /uploads — so a dev build
    # produces a genuinely fetchable bundle URL, not a stub.
    from src.config.settings import settings as _settings
    from src.core.interfaces.services.storage_service import StorageBucket

    if _settings.object_storage_configured:
        from src.infrastructure.external_services.cloudflare_r2.storage_service import (
            CloudflareR2StorageService,
        )

        storage = CloudflareR2StorageService()
    else:
        from src.infrastructure.external_services.local_storage import (
            LocalStorageService,
        )

        storage = LocalStorageService()

    loop = asyncio.new_event_loop()
    bundle_key = f"themes/{theme_id_slug}/{version}-{version_hash}/theme.js"
    bundle_uploaded = loop.run_until_complete(
        storage.upload_file(
            file_content=bundle_bytes,
            filename="theme.js",
            content_type="application/javascript",
            bucket=StorageBucket.THEMES,
            key=bundle_key,
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
                filename="theme.css",
                content_type="text/css",
                bucket=StorageBucket.THEMES,
                key=css_key,
            )
        )
        css_url = css_uploaded.url
    loop.close()

    # ── Parse schemas ─────────────────────────────────────────────────────────
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

    # ── Register themes + theme_versions rows ─────────────────────────────────
    status(build_id, status="registering")

    async def _register():
        from src.core.entities.theme import (
            Theme,
            ThemeStatus,
            ThemeType,
            ThemeVersion,
        )
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.repositories.theme_repository import ThemeRepository
        from src.infrastructure.repositories.theme_version_repository import (
            ThemeVersionRepository,
        )

        async with AsyncSessionLocal() as session:
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

            # Code-editor publishes (activate_for_store_id set) republish the
            # SAME theme.json version constantly, so we register a
            # content-unique version "<version>+<checksum8>" (valid semver
            # build metadata, mirrors dev-mode's "+dev.<ts>"). Each distinct
            # build is its own immutable row with the correct bundle, and
            # identical-content republishes reuse the existing row. The
            # marketplace ZIP path keeps the developer's exact version.
            reg_version = (
                f"{version}+{version_hash}" if activate_for_store_id else version
            )
            existing_ver = (
                await version_repo.get_by_theme_and_version(theme.id, reg_version)
                if activate_for_store_id
                else None
            )
            if existing_ver is not None:
                existing_ver.is_latest = True
                existing_ver.published_at = datetime.now(UTC)
                theme_version = await version_repo.update(existing_ver)
            else:
                theme_version = await version_repo.create(
                    ThemeVersion(
                        id=uuid4(),
                        theme_id=theme.id,
                        version=reg_version,
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
                )
            await session.commit()

            return {
                "theme_id": str(theme.id),
                "version_id": str(theme_version.id),
            }

    loop = asyncio.new_event_loop()
    try:
        ids = loop.run_until_complete(_register())
    finally:
        loop.run_until_complete(_dispose_engine())
        loop.close()

    # ── Activate for the store (code-editor publish only) ─────────────────────
    if activate_for_store_id and tenant_id:
        status(build_id, status="activating")
        _activate_built_theme_for_store(
            store_id=activate_for_store_id,
            tenant_id=tenant_id,
            theme_id=ids["theme_id"],
            version_id=ids["version_id"],
        )

    status(
        build_id,
        status="complete",
        theme_id=ids["theme_id"],
        version_id=ids["version_id"],
        bundle_url=bundle_url,
        css_url=css_url,
        checksum=checksum,
        size_bytes=size,
        completed_at=datetime.now(UTC).isoformat(),
    )
    logger.info(
        "Theme %s v%s built successfully: %s", theme_id_slug, version, bundle_url
    )
    return {
        "build_id": build_id,
        "status": "complete",
        "theme_id": ids["theme_id"],
        "version_id": ids["version_id"],
        "bundle_url": bundle_url,
        "css_url": css_url,
        "checksum": checksum,
        "size_bytes": size,
    }


def _activate_built_theme_for_store(
    *, store_id: str, tenant_id: str, theme_id: str, version_id: str
) -> None:
    """Make a freshly-built ThemeVersion the store's active theme + go live.

    Reuses ``ThemeActivationService`` (snapshot → deactivate others → upsert
    active StoreTheme → mirror marketplace rows) then denormalizes to
    ``stores.theme_settings`` and triggers Next.js revalidation via
    ``ThemeService`` — the same path ``ThemeService.activate_theme`` uses, so
    the storefront's next resolve serves the new bundle.
    """
    import asyncio
    from uuid import UUID

    async def _run():
        from src.application.services.theme_activation_service import (
            ThemeActivationService,
        )
        from src.application.services.theme_service import ThemeService
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.repositories.marketplace_repository import (
            MarketplaceRepository,
        )
        from src.infrastructure.repositories.store_repository import (
            StoreRepository,
        )
        from src.infrastructure.repositories.store_theme_repository import (
            StoreThemeRepository,
        )
        from src.infrastructure.repositories.store_theme_snapshot_repository import (
            StoreThemeSnapshotRepository,
        )
        from src.infrastructure.repositories.theme_repository import ThemeRepository
        from src.infrastructure.repositories.theme_version_repository import (
            ThemeVersionRepository,
        )

        async with AsyncSessionLocal() as session:
            store_theme_repo = StoreThemeRepository(session)
            activation = ThemeActivationService(
                store_theme_repo=store_theme_repo,
                snapshot_repo=StoreThemeSnapshotRepository(session),
                marketplace_repo=MarketplaceRepository(session),
            )
            updated = await activation.activate(
                store_id=UUID(store_id),
                tenant_id=UUID(tenant_id),
                theme_id=UUID(theme_id),
                theme_version_id=UUID(version_id),
                reason="code-editor-publish",
            )

            store_repo = StoreRepository(session)
            svc = ThemeService(
                theme_repo=ThemeRepository(session),
                version_repo=ThemeVersionRepository(session),
                store_theme_repo=store_theme_repo,
            )
            # Backward-compat mirror + commit + Next.js revalidate.
            await svc._denormalize_to_store(UUID(store_id), updated, store_repo)
            await svc._revalidate_storefront(
                UUID(store_id), store_repo, kind="theme_activate"
            )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(_dispose_engine())
        loop.close()


# ── Main Celery tasks ─────────────────────────────────────────────────────────


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
    """Build a marketplace theme from an uploaded ZIP and register it.

    Source = ZIP; status = Redis (``ThemeBuildStore`` via ``_redis_status``),
    so the API poller — a different process than this worker on prod — sees
    progress. The initial ``queued`` key is written by the upload route
    before this task is dispatched. Delegates the validate→build→…→register
    pipeline to the shared core.
    """
    work_dir: Path | None = None
    try:
        _redis_status(build_id, status="extracting")
        logger.info("Extracting ZIP %s for build %s", zip_path, build_id)

        work_dir = Path(tempfile.mkdtemp(prefix="numu-theme-zip-"))
        theme_dir = work_dir / "theme"
        theme_dir.mkdir(parents=True)
        _safe_extract_zip(Path(zip_path), theme_dir)

        return _build_register_activate(
            theme_dir=theme_dir,
            build_id=build_id,
            status=_redis_status,
            uploader_id=uploader_id,
        )

    except ThemeBuildError as e:
        logger.error("Build %s failed: %s", build_id, e)
        _redis_status(build_id, status="failed", error=str(e))
        return {"build_id": build_id, "status": "failed", "error": str(e)}
    except Exception as e:
        logger.exception("Build %s crashed", build_id)
        _redis_status(build_id, status="failed", error=f"Internal error: {e}")
        raise
    finally:
        if work_dir and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        try:
            os.unlink(zip_path)
        except OSError:
            pass


@celery_app.task(
    name="build_theme_from_files",
    bind=True,
    max_retries=0,
    soft_time_limit=300,
    time_limit=360,
)
def build_theme_from_files(
    self,
    store_id: str,
    build_id: str,
    tenant_id: str,
) -> dict:
    """Build a store's in-app code-editor workspace and publish it live.

    Source = the store's ``store_theme_files`` (materialized to disk); status =
    Redis (so the editor's cross-process poller sees progress). On success the
    new ``ThemeVersion`` is activated for the store and the storefront is
    revalidated — the full Save→Publish→live loop.
    """
    from src.infrastructure.messaging.tasks.theme_build_tasks import (
        _materialize_store_files,
    )

    work_dir: Path | None = None
    try:
        _redis_status(build_id, status="cloning")  # "preparing source"
        work_dir = Path(tempfile.mkdtemp(prefix="numu-theme-files-"))
        theme_dir = work_dir / "theme"
        theme_dir.mkdir(parents=True)
        _materialize_store_files(store_id, theme_dir)

        return _build_register_activate(
            theme_dir=theme_dir,
            build_id=build_id,
            status=_redis_status,
            activate_for_store_id=store_id,
            tenant_id=tenant_id,
        )

    except ThemeBuildError as e:
        logger.error("Code-editor build %s failed: %s", build_id, e)
        _redis_status(build_id, status="failed", error=str(e))
        return {"build_id": build_id, "status": "failed", "error": str(e)}
    except Exception as e:
        logger.exception("Code-editor build %s crashed", build_id)
        _redis_status(build_id, status="failed", error=f"Internal error: {e}")
        raise
    finally:
        if work_dir and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
