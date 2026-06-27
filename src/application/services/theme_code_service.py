"""Theme code-editor service — business logic for the in-app file workspace.

Backs Online Store → Edit code. CRUD over a store's ``store_theme_files``
plus a Scaffold action that seeds a complete, buildable V3 starter theme so
the merchant has something real to edit. Publishing the workspace is a thin
dispatch to the existing external-theme build pipeline (see the route layer),
so this service deliberately knows nothing about R2 / Celery / bundles.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status

from src.core.entities.theme import StoreThemeFile
from src.infrastructure.repositories.store_theme_file_repository import (
    StoreThemeFileRepository,
)

logger = logging.getLogger(__name__)

# Bundled theme sources the editor can seed from. `v3_starter` is the generic,
# always-present fallback; the real V3 themes (empire-v3, bazar-v3, …) are
# bundled by scripts/sync_theme_scaffolds.py and named by their theme.json id,
# so an active-theme slug maps straight to a source dir.
_SCAFFOLD_BASE = (
    Path(__file__).resolve().parents[2] / "infrastructure" / "theme_scaffold"
)
_DEFAULT_SOURCE = "v3_starter"
# Back-compat alias — the default starter dir.
_SCAFFOLD_DIR = _SCAFFOLD_BASE / _DEFAULT_SOURCE

_MAX_FILE_BYTES = 512 * 1024  # 512 KB per file — generous for source, blocks abuse
_MAX_PATH_LEN = 300


class ThemeCodeService:
    """CRUD + scaffold over a store's editable theme source files."""

    def __init__(self, file_repo: StoreThemeFileRepository) -> None:
        self.file_repo = file_repo

    # ── Path safety ──────────────────────────────────────────────────────────

    @staticmethod
    def _safe_path(path: str) -> str:
        """Normalize + validate a workspace-relative path.

        Rejects absolute paths, parent traversal, backslashes, and empties so a
        stored path can never escape the build temp dir when materialized.
        """
        cleaned = (path or "").strip().replace("\\", "/").lstrip("/")
        if not cleaned:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="File path is required",
            )
        if len(cleaned) > _MAX_PATH_LEN:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"File path too long (max {_MAX_PATH_LEN})",
            )
        parts = cleaned.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid file path",
            )
        return cleaned

    # ── CRUD ─────────────────────────────────────────────────────────────────

    async def list_files(self, store_id: UUID) -> list[StoreThemeFile]:
        return await self.file_repo.list_for_store(store_id)

    async def read_file(self, store_id: UUID, path: str) -> StoreThemeFile:
        safe = self._safe_path(path)
        f = await self.file_repo.get(store_id, safe)
        if f is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {safe}",
            )
        return f

    async def write_file(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        path: str,
        content: str,
    ) -> StoreThemeFile:
        safe = self._safe_path(path)
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large (max {_MAX_FILE_BYTES // 1024} KB)",
            )
        return await self.file_repo.upsert(
            store_id=store_id, tenant_id=tenant_id, path=safe, content=content
        )

    async def delete_file(self, store_id: UUID, path: str) -> None:
        safe = self._safe_path(path)
        removed = await self.file_repo.delete(store_id, safe)
        if not removed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {safe}",
            )

    # ── Scaffold ─────────────────────────────────────────────────────────────

    @staticmethod
    def _slugify(value: str, fallback: str) -> str:
        slug = re.sub(r"[^a-z0-9-]", "", value.lower().replace(" ", "-")).strip("-")
        return slug or fallback

    @staticmethod
    def source_dir(source: str | None) -> Path:
        """Resolve a bundled source name → its dir, falling back to the
        default starter when the requested source isn't bundled (e.g. the
        store's active theme has no source on the server)."""
        candidate = _SCAFFOLD_BASE / (source or _DEFAULT_SOURCE)
        if source and candidate.is_dir() and candidate.name != "":
            # Guard against path traversal via a crafted source name.
            try:
                candidate.resolve().relative_to(_SCAFFOLD_BASE.resolve())
            except ValueError:
                return _SCAFFOLD_BASE / _DEFAULT_SOURCE
            return candidate
        return _SCAFFOLD_BASE / _DEFAULT_SOURCE

    @staticmethod
    def _apply_identity(
        files: dict[str, str],
        *,
        theme_id: str,
        theme_name: str,
        author: str,
        version: str,
        pkg_name: str,
    ) -> None:
        """Force the seeded theme's identity to store-unique values.

        v3_starter carries placeholders (handled by token replacement); real
        theme sources carry the upstream id (e.g. "empire-v3"). Rewriting
        theme.json/package.json here makes every seeded workspace its OWN
        theme, so a merchant's Publish never collides with the shared
        marketplace theme (the build keys themes by theme.json `id`)."""
        if "theme.json" in files:
            try:
                tj = json.loads(files["theme.json"])
                tj["id"] = theme_id
                tj["name"] = theme_name
                tj["author"] = author
                tj["version"] = version
                files["theme.json"] = (
                    json.dumps(tj, ensure_ascii=False, indent=2) + "\n"
                )
            except json.JSONDecodeError:
                pass
        if "package.json" in files:
            try:
                pj = json.loads(files["package.json"])
                pj["name"] = pkg_name
                pj["version"] = version
                files["package.json"] = (
                    json.dumps(pj, ensure_ascii=False, indent=2) + "\n"
                )
            except json.JSONDecodeError:
                pass

    @classmethod
    def render_scaffold(
        cls,
        *,
        theme_id: str,
        theme_name: str,
        author: str = "NUMU",
        version: str = "1.0.0",
        pkg_name: str | None = None,
        source: str | None = None,
    ) -> dict[str, str]:
        """Read a bundled source and produce {path: content}.

        `source` selects which bundled theme to seed from (default
        v3_starter; unknown → falls back to v3_starter). Placeholders are
        filled (for v3_starter) and the theme identity is forced to the
        passed `theme_id`/`theme_name` so the workspace is a store-owned copy.

        Kept as a classmethod (no DB) so it's unit-testable in isolation.
        """
        src_dir = cls.source_dir(source)
        if not src_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Theme scaffold template is missing from the build.",
            )
        resolved_pkg = pkg_name or f"numu-theme-{theme_id}"
        replacements = {
            "__THEME_ID__": theme_id,
            "__THEME_NAME__": theme_name,
            "__AUTHOR__": author,
            "__VERSION__": version,
            "__PKG_NAME__": resolved_pkg,
        }
        files: dict[str, str] = {}
        for fp in sorted(src_dir.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(src_dir).as_posix()
            text = fp.read_text(encoding="utf-8")
            for token, value in replacements.items():
                text = text.replace(token, value)
            files[rel] = text
        # Enforce store-unique identity (no-op-equivalent for v3_starter, which
        # already substituted the same values via tokens).
        cls._apply_identity(
            files,
            theme_id=theme_id,
            theme_name=theme_name,
            author=author,
            version=version,
            pkg_name=resolved_pkg,
        )
        return files

    async def scaffold(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        theme_name: str,
        theme_id: str | None = None,
        source: str | None = None,
        author: str = "NUMU",
        overwrite: bool = False,
    ) -> int:
        """Seed the workspace with a buildable theme.

        ``source`` selects which bundled theme to seed from — typically the
        store's active-theme slug (so a store on Empire seeds Empire's source),
        falling back to v3_starter when not bundled.

        Refuses to clobber a non-empty workspace unless ``overwrite`` is set
        (then it wipes first) — protects merchant edits from an accidental
        re-scaffold.
        """
        existing = await self.file_repo.count_for_store(store_id)
        if existing and not overwrite:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A theme workspace already exists. Pass overwrite=true to reset it.",
            )
        if existing and overwrite:
            await self.file_repo.delete_all_for_store(store_id)

        # Store-unique theme id so a merchant's Publish registers its OWN theme
        # (the build keys by theme.json id) instead of colliding with the
        # shared marketplace theme it was seeded from.
        store_suffix = str(store_id).replace("-", "")[:8]
        base_id = self._slugify(
            theme_id or source or theme_name, fallback=f"store-{store_suffix}"
        )
        resolved_id = f"{base_id}-{store_suffix}"
        files = self.render_scaffold(
            theme_id=resolved_id,
            theme_name=theme_name.strip() or "My Theme",
            author=author,
            source=source,
        )
        count = await self.file_repo.bulk_upsert(
            store_id=store_id, tenant_id=tenant_id, files=files
        )
        logger.info(
            "theme_workspace_scaffolded",
            extra={
                "store_id": str(store_id),
                "theme_id": resolved_id,
                "file_count": count,
            },
        )
        return count
