"""V3 Theme Editor service layer.

Handles auto-save drafts, publish with Dual-Write, version history,
restore, and BYOT initialization.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from src.application.services.image_transform import sanitize_transform_settings
from src.application.services.theme_v3_mappers import (
    map_v3_to_legacy_store_settings,
    normalize_legacy_to_v3,
)
from src.application.services.theme_v3_presets import reconcile_v3_customization
from src.core.entities.theme_customization_version import ThemeCustomizationVersion
from src.core.entities.theme_settings_v3 import ThemeSettingsV3

logger = logging.getLogger(__name__)

# Cap how many autosave rows we retain per store. Older autosaves are
# pruned on each new write. Published versions are NEVER pruned by this cap.
AUTOSAVE_RETENTION = 20


class StaleEtagError(Exception):
    """Raised by autosave_draft when expected_etag doesn't match the
    current store_theme.updated_at — i.e. another tab saved while this
    one was editing. Routes catch this and return 409.
    """

    def __init__(self, current_etag: str | None, current_draft: dict[str, Any]):
        super().__init__(
            "Stale etag — another editor saved since this draft was loaded."
        )
        self.current_etag = current_etag
        self.current_draft = current_draft


def _customization_hash(payload: dict[str, Any] | None) -> str:
    """Stable content hash of a customization payload.

    Used as the published-revision fingerprint: the publish response carries
    it so the hub can cache-bust the storefront with ``?v=<hash>`` and so the
    post-commit freshness read-back can confirm a *separate* session sees the
    exact bytes we published.
    """
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _etag_from(value: datetime | str | None) -> str | None:
    """Encode a store_theme.updated_at as a stable etag string.

    We use the ISO timestamp directly. Microsecond precision means even
    rapid saves produce distinct etags; if the DB drops fractional
    seconds the comparison still works because both sides round trip
    through the same column.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _normalize_etag(value: str | None) -> str | None:
    """Strip weak-validator wrapping (``W/"..."``) and surrounding quotes.

    A gzip-aware proxy / CDN (Nginx, Vercel) can rewrite a strong ``ETag``
    into a weak one — ``2026-…+00:00`` becomes ``W/"2026-…+00:00"`` — before
    it reaches the browser. The client then echoes that wrapped form back as
    ``If-Match`` / ``expected_etag``, which would never string-equal the bare
    ISO timestamp we store. Normalizing both sides before comparing makes the
    optimistic-concurrency check survive that transformation.
    """
    if value is None:
        return None
    v = value.strip()
    if v.startswith("W/"):
        v = v[2:].strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


class ThemeV3Service:
    """Service for V3 theme editor operations."""

    def __init__(self, store_theme_repo, version_repo, store_repo=None):
        self._store_theme_repo = store_theme_repo
        self._version_repo = version_repo
        self._store_repo = store_repo

    async def get_draft(self, store_id: UUID) -> dict[str, Any]:
        """Get the current V3 draft. Falls back to normalizing V2 data.

        Kept for backward compatibility — `get_draft_with_etag` is the
        preferred entry point now.
        """
        result = await self.get_draft_with_etag(store_id)
        return result["draft"]

    async def get_draft_with_etag(self, store_id: UUID) -> dict[str, Any]:
        """Like get_draft but also returns an etag the client can echo
        back on autosave for optimistic concurrency control.
        """
        store_theme = await self._store_theme_repo.get_active_for_store(store_id)
        if not store_theme:
            return {"draft": {}, "etag": None}

        etag = _etag_from(getattr(store_theme, "updated_at", None))

        # Theme presets + known section types, used to repair a stored
        # customization whose sections the active theme can't render (stale
        # generic sections from a previous theme / snapshot restore). Mirrors
        # the storefront bundle's selectTemplateSections fallback so the editor
        # lists exactly what the storefront renders. Read-time + non-destructive.
        section_schemas = getattr(store_theme, "section_schemas", None)
        presets = getattr(store_theme, "presets", None)

        # Prefer V3 draft if present and well-formed
        if (
            store_theme.draft_customization_v3
            and store_theme.draft_customization_v3.get("schema_version") == 3
        ):
            draft = reconcile_v3_customization(
                store_theme.draft_customization_v3, section_schemas, presets
            )
            return {"draft": draft, "etag": etag}

        # Then V3 published
        if (
            store_theme.customization_v3
            and store_theme.customization_v3.get("schema_version") == 3
        ):
            published = reconcile_v3_customization(
                store_theme.customization_v3, section_schemas, presets
            )
            return {"draft": published, "etag": etag}

        # Fall back to normalizing legacy data (Dual-Read)
        legacy = store_theme.customization or store_theme.draft_customization or {}
        if legacy:
            v3 = normalize_legacy_to_v3(legacy)
            return {"draft": v3.model_dump(), "etag": etag}

        return {"draft": {}, "etag": etag}

    async def get_published(self, store_id: UUID) -> dict[str, Any]:
        """Get the currently published V3 customization (no draft)."""
        store_theme = await self._store_theme_repo.get_active_for_store(store_id)
        if not store_theme:
            return {}
        if (
            store_theme.customization_v3
            and store_theme.customization_v3.get("schema_version") == 3
        ):
            return reconcile_v3_customization(
                store_theme.customization_v3,
                getattr(store_theme, "section_schemas", None),
                getattr(store_theme, "presets", None),
            )
        legacy = store_theme.customization or {}
        if legacy:
            return normalize_legacy_to_v3(legacy).model_dump()
        return {}

    async def autosave_draft(
        self,
        store_id: UUID,
        payload: dict[str, Any],
        user_id: UUID | None = None,
        change_summary: str | None = None,
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        """Auto-save a V3 draft with Dual-Write to legacy columns.

        Skips writing a version row if the payload is unchanged from the
        previous draft (idempotent autosave). Prunes older autosaves
        beyond AUTOSAVE_RETENTION.

        When `expected_etag` is provided, raises StaleEtagError if it
        doesn't match the current store_theme.updated_at — meaning a
        different editor saved since this draft was loaded. The route
        layer maps that to 409.

        First-write callers (no prior etag to compare) can omit
        `expected_etag` and skip the conflict check; this preserves
        existing test fixtures and bootstrap flows. Production clients
        should always pass it after the first /draft fetch.
        """
        store_theme = await self._store_theme_repo.get_active_for_store(store_id)
        if not store_theme:
            raise ValueError(f"No active theme for store {store_id}")

        # Optimistic concurrency guard. We do this BEFORE Pydantic
        # validation so a conflict with stale data short-circuits even
        # if the payload is malformed.
        current_etag = _etag_from(getattr(store_theme, "updated_at", None))
        if expected_etag is not None and _normalize_etag(
            current_etag
        ) != _normalize_etag(expected_etag):
            current = await self.get_draft_with_etag(store_id)
            raise StaleEtagError(
                current_etag=current["etag"],
                current_draft=current["draft"],
            )

        # Validate V3 payload (raises ValidationError on bad input — caller
        # converts to 400). Includes external_theme URL allowlist enforcement.
        v3 = ThemeSettingsV3(**payload)
        # Clamp any image focal/zoom/rotation transform to safe ranges so a
        # buggy/hostile client can't persist out-of-range metadata (the
        # storefront also clamps at render — this is defense-in-depth).
        v3_dict = sanitize_transform_settings(v3.model_dump())

        # Idempotency guard: skip the write if nothing changed.
        prev_draft = store_theme.draft_customization_v3 or {}
        is_unchanged = prev_draft == v3_dict

        # DUAL-WRITE: Write V3 to new column
        store_theme.draft_customization_v3 = v3_dict
        # DUAL-WRITE: Map V3 -> legacy and write to old column
        store_theme.draft_customization = map_v3_to_legacy_store_settings(v3)

        await self._store_theme_repo.update(store_theme)

        if is_unchanged:
            return v3_dict

        # Create autosave version row
        version = ThemeCustomizationVersion(
            store_id=store_id,
            theme_id=v3.theme_id,
            settings_blob=v3_dict,
            change_summary=change_summary or "Auto-save",
            created_by=user_id,
            is_published=False,
            is_autosave=True,
        )
        await self._version_repo.create(version)

        # Retention: keep last N autosaves (published versions are kept
        # forever — pruning runs only over `is_autosave=True`).
        try:
            await self._version_repo.prune_autosaves(
                store_id=store_id, keep=AUTOSAVE_RETENTION
            )
        except Exception as exc:  # pragma: no cover — non-fatal
            logger.warning(
                "v3_autosave_prune_failed",
                extra={"store_id": str(store_id), "error": str(exc)},
            )

        logger.info(
            "v3_autosave_saved",
            extra={"store_id": str(store_id), "version_id": str(version.id)},
        )
        return v3_dict

    async def publish(
        self,
        store_id: UUID,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Publish V3 draft with Dual-Write to all columns.

        Ordering contract (the cache-freshness fix): this method PERSISTS and
        then COMMITS the transaction before returning, so the caller's cache
        invalidation + storefront revalidation run strictly against committed
        data. It does NOT itself invalidate Redis or revalidate the storefront
        — the route owns that (it already holds the store + cache) and runs it
        after this returns. See :func:`commit_and_restore_rls`.

        Returns a dict:
            {
              "published": <v3 dict>,
              "revision_id": <published version id>,
              "content_hash": <sha256 of published payload>,
              "verified": <bool: a separate session can read the new payload>,
            }
        """
        store_theme = await self._store_theme_repo.get_active_for_store(store_id)
        if not store_theme:
            raise ValueError(f"No active theme for store {store_id}")

        draft_v3 = store_theme.draft_customization_v3
        if not draft_v3 or draft_v3.get("schema_version") != 3:
            raise ValueError("No V3 draft to publish")

        # Re-validate before writing (defense in depth — the draft could have
        # been written under an older schema and we don't want to publish it
        # without validation).
        v3 = ThemeSettingsV3(**draft_v3)
        v3_dict = sanitize_transform_settings(v3.model_dump())

        # DUAL-WRITE: Publish V3
        store_theme.customization_v3 = v3_dict
        store_theme.draft_customization_v3 = {}

        # DUAL-WRITE: Map V3 -> legacy and publish to old columns
        legacy = map_v3_to_legacy_store_settings(v3)
        store_theme.customization = legacy
        store_theme.draft_customization = {}

        await self._store_theme_repo.update(store_theme)

        # Create published version record
        version = ThemeCustomizationVersion(
            store_id=store_id,
            theme_id=v3.theme_id,
            settings_blob=v3_dict,
            change_summary="Published",
            created_by=user_id,
            is_published=True,
            is_autosave=False,
        )
        await self._version_repo.create(version)

        content_hash = _customization_hash(v3_dict)

        # ── COMMIT before any cache/revalidation work happens ───────────────
        # The request session otherwise commits in get_db_session's finalizer
        # AFTER the handler returns — i.e. AFTER the route revalidates the
        # storefront, which would let a racing read re-cache the stale row.
        from src.infrastructure.database.connection import commit_and_restore_rls

        session = self._store_theme_repo.session
        await commit_and_restore_rls(session)

        # ── Freshness read-back from a SEPARATE session ─────────────────────
        # Proves the published bytes are committed and visible to other
        # connections (exactly what the storefront's next fetch will see),
        # not merely flushed inside our own open transaction.
        verified = await self._verify_published(store_id, content_hash)
        if not verified:
            logger.warning(
                "v3_publish_freshness_unverified",
                extra={"store_id": str(store_id), "content_hash": content_hash},
            )

        logger.info(
            "v3_published",
            extra={
                "store_id": str(store_id),
                "revision_id": str(version.id),
                "content_hash": content_hash,
                "verified": verified,
            },
        )
        return {
            "published": v3_dict,
            "revision_id": str(version.id),
            "content_hash": content_hash,
            "verified": verified,
        }

    async def _verify_published(self, store_id: UUID, expected_hash: str) -> bool:
        """Confirm a separate DB session can read the just-published payload.

        Opens an independent tenant-scoped session and re-hashes the persisted
        ``customization_v3``; returns True iff it matches ``expected_hash``.
        Any error degrades to False (logged by the caller) — the publish still
        succeeds, the merchant just gets a "refresh may be delayed" signal.
        """
        from src.infrastructure.database.connection import tenant_scoped_session
        from src.infrastructure.repositories.store_theme_repository import (
            StoreThemeRepository,
        )

        try:
            async with tenant_scoped_session() as session:
                repo = StoreThemeRepository(session)
                fresh = await repo.get_active_for_store(store_id)
                if not fresh:
                    return False
                return _customization_hash(fresh.customization_v3) == expected_hash
        except Exception as exc:  # pragma: no cover — non-fatal best effort
            logger.warning(
                "v3_publish_verify_failed",
                extra={"store_id": str(store_id), "error": str(exc)},
            )
            return False

    async def get_versions(
        self,
        store_id: UUID,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """List version history for a store."""
        versions = await self._version_repo.list_by_store(
            store_id=store_id,
            page=page,
            per_page=per_page,
        )
        return [
            {
                "id": str(v.id),
                "theme_id": v.theme_id,
                "change_summary": v.change_summary,
                "is_published": v.is_published,
                "is_autosave": v.is_autosave,
                "version_label": v.version_label,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "created_by": str(v.created_by) if v.created_by else None,
            }
            for v in versions
        ]

    async def get_version_payload(
        self,
        store_id: UUID,
        version_id: UUID,
    ) -> dict[str, Any]:
        """Return a single version's settings blob (read-only, for the diff view).

        Used by the editor's PrePublishDiffDialog to load the last-published
        snapshot to diff the current draft against. Tenant-scoped: a version
        belonging to another store is treated as not found.
        """
        version = await self._version_repo.get_by_id(version_id)
        if not version or version.store_id != store_id:
            raise ValueError(f"Version {version_id} not found for store {store_id}")
        return version.settings_blob or {}

    async def restore_version(
        self,
        store_id: UUID,
        version_id: UUID,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Restore a previous version as the current draft."""
        version = await self._version_repo.get_by_id(version_id)
        if not version or version.store_id != store_id:
            raise ValueError(f"Version {version_id} not found for store {store_id}")

        return await self.autosave_draft(
            store_id=store_id,
            payload=version.settings_blob,
            user_id=user_id,
            change_summary=f"Restored from version {version_id}",
        )

    async def discard_draft(self, store_id: UUID) -> dict[str, Any]:
        """Discard V3 draft and revert to published state."""
        store_theme = await self._store_theme_repo.get_active_for_store(store_id)
        if not store_theme:
            raise ValueError(f"No active theme for store {store_id}")

        published = store_theme.customization_v3 or {}
        store_theme.draft_customization_v3 = {}
        store_theme.draft_customization = {}
        await self._store_theme_repo.update(store_theme)
        return published
