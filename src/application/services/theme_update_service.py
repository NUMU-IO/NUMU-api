"""Theme update detection service (Phase 5.1).

Turns a published-version bump into a per-install notification. For each
active marketplace install on a store, compares the installed version's
schemas against the theme's latest published version via
``classify_theme_update`` and records a ``ThemeUpdateNotification`` (manual
or automatic). Idempotent: re-running refreshes a pending notification rather
than duplicating it. NEVER mutates a store — applying is a separate,
merchant-confirmed, snapshot-first step (the route delegates to
``MarketplaceService.install_theme`` + ``activate_theme``).
"""

from __future__ import annotations

from typing import Any

from src.application.services.theme_update_classifier import classify_theme_update
from src.core.entities.theme_update_notification import ThemeUpdateNotification


def _schemas_of(version: Any) -> dict[str, Any]:
    """Pull the diff-relevant schemas off a marketplace version entity."""
    if version is None:
        return {}
    return {
        "settings_schema": getattr(version, "settings_schema", None),
        "section_schemas": getattr(version, "section_schemas", None),
    }


def build_notification(
    *,
    store_id: Any,
    tenant_id: Any,
    marketplace_theme_id: Any,
    installed_version: Any,
    latest_version: Any,
) -> ThemeUpdateNotification | None:
    """Pure: classify installed→latest and build a notification, or None when
    there's nothing newer to adopt. Testable with simple stand-in version
    objects (only ``.id``/``.version_string``/``.settings_schema``/
    ``.section_schemas``/``.release_notes`` are read)."""
    if latest_version is None:
        return None
    installed_id = getattr(installed_version, "id", None)
    if installed_id is not None and installed_id == latest_version.id:
        return None  # already on the latest version

    verdict = classify_theme_update(
        _schemas_of(installed_version), _schemas_of(latest_version)
    )
    return ThemeUpdateNotification(
        store_id=store_id,
        tenant_id=tenant_id,
        theme_id=marketplace_theme_id,
        from_version_id=installed_id,
        to_version_id=latest_version.id,
        from_version=(getattr(installed_version, "version_string", "") or "")
        if installed_version
        else "",
        to_version=getattr(latest_version, "version_string", "") or "",
        classification=verdict["classification"],
        changes=verdict["changes"],
        release_notes=getattr(latest_version, "release_notes", "") or "",
        status="pending",
    )


class ThemeUpdateService:
    """Detects newer published versions for a store's active installs."""

    def __init__(self, marketplace_repo: Any, notification_repo: Any) -> None:
        self._mp = marketplace_repo
        self._notifs = notification_repo

    async def check_store(self, store: Any) -> list[ThemeUpdateNotification]:
        """Scan the store's active marketplace installs; create/refresh a
        pending notification for each that has a newer published version.
        Returns the notifications created or refreshed this run."""
        out: list[ThemeUpdateNotification] = []
        installs = await self._mp.list_installations(store.id)
        for inst in installs:
            if not getattr(inst, "is_active", True):
                continue
            theme_id = inst.marketplace_theme_id
            installed_version_id = inst.marketplace_version_id
            latest = await self._mp.get_latest_published_version(theme_id)
            if not latest or latest.id == installed_version_id:
                continue  # no newer published version
            installed = (
                await self._mp.get_version_by_id(installed_version_id)
                if installed_version_id
                else None
            )
            notif = build_notification(
                store_id=store.id,
                tenant_id=store.tenant_id,
                marketplace_theme_id=theme_id,
                installed_version=installed,
                latest_version=latest,
            )
            if notif is None:
                continue
            existing = await self._notifs.get_for_version(store.id, notif.to_version_id)
            if existing:
                # Refresh a still-pending notification (schemas may have been
                # re-diffed); leave applied/skipped ones alone.
                if existing.status == "pending":
                    existing.classification = notif.classification
                    existing.changes = notif.changes
                    existing.release_notes = notif.release_notes
                    out.append(await self._notifs.update(existing))
                continue
            out.append(await self._notifs.create(notif))
        return out
