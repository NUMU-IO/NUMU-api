"""Theme code-editor service — business logic for the in-app file workspace.

Backs Online Store → Edit code. CRUD over a store's ``store_theme_files``
plus a Scaffold action that seeds a complete, buildable V3 starter theme so
the merchant has something real to edit. Publishing the workspace is a thin
dispatch to the existing external-theme build pipeline (see the route layer),
so this service deliberately knows nothing about R2 / Celery / bundles.
"""

from __future__ import annotations

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

# Bundled, known-good V3 starter (copied from @numueg/theme-cli's `init`
# scaffold). Placeholders are filled at scaffold time.
_SCAFFOLD_DIR = (
    Path(__file__).resolve().parents[2]
    / "infrastructure"
    / "theme_scaffold"
    / "v3_starter"
)

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

    @classmethod
    def render_scaffold(
        cls,
        *,
        theme_id: str,
        theme_name: str,
        author: str = "NUMU",
        version: str = "1.0.0",
        pkg_name: str | None = None,
    ) -> dict[str, str]:
        """Read the bundled starter and fill placeholders → {path: content}.

        Kept as a classmethod (no DB) so it's unit-testable in isolation.
        """
        if not _SCAFFOLD_DIR.is_dir():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Theme scaffold template is missing from the build.",
            )
        replacements = {
            "__THEME_ID__": theme_id,
            "__THEME_NAME__": theme_name,
            "__AUTHOR__": author,
            "__VERSION__": version,
            "__PKG_NAME__": pkg_name or f"numu-theme-{theme_id}",
        }
        files: dict[str, str] = {}
        for fp in sorted(_SCAFFOLD_DIR.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(_SCAFFOLD_DIR).as_posix()
            text = fp.read_text(encoding="utf-8")
            for token, value in replacements.items():
                text = text.replace(token, value)
            files[rel] = text
        return files

    async def scaffold(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        theme_name: str,
        theme_id: str | None = None,
        author: str = "NUMU",
        overwrite: bool = False,
    ) -> int:
        """Seed the workspace with a buildable starter theme.

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

        resolved_id = self._slugify(
            theme_id or theme_name, fallback=f"store-{str(store_id)[:8]}"
        )
        files = self.render_scaffold(
            theme_id=resolved_id,
            theme_name=theme_name.strip() or "My Theme",
            author=author,
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
