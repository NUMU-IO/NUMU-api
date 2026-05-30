"""Single source of truth for switching a store's active theme.

Three call sites funnel through ``ThemeActivationService.activate``:

1. **Marketplace activate** (``MarketplaceService.activate_theme``) — merchant
   clicks Activate on a marketplace card. Sets ``marketplace_installation_id``
   so this service mirrors ``store_themes.is_active`` to
   ``marketplace_theme_installations.is_active`` and the customizer agrees
   with the catalog.

2. **Dev-mode connect** (``POST /stores/{id}/themes/external/dev-mode``) — BYOT
   developer pastes a vite dev URL. ``marketplace_installation_id`` is omitted
   so any active marketplace install gets cleared (the dev-mode bundle is the
   new active theme; the previous marketplace one is conceptually paused).

3. **V2 store-themes activate**
   (``ThemeService.activate_theme`` → ``POST /stores/{id}/themes/v2/installations/{id}/activate``)
   — merchant activates a non-marketplace installed theme. Same case as
   dev-mode for the marketplace mirror: no marketplace install is active.

Guarantees:
  1. A snapshot is written to ``store_theme_snapshots`` BEFORE any mutation
     when there's a prior active row.
  2. ``store_themes.is_active`` is the canonical active-row pointer; this
     service flips it atomically.
  3. ``marketplace_theme_installations.is_active`` mirrors
     ``store_themes.is_active`` when ``marketplace_theme_id`` is supplied; is
     explicitly cleared otherwise (no stale marketplace install lingering
     active after a dev-mode or V2 swap).
  4. ``customization_v3`` is replaced only when the caller supplies
     ``seed_customization_v3``; otherwise the existing row's payload is
     preserved (avoids wiping merchant work when the V2 path just toggles
     the active flag).

Phase 3a (2026-05-26) — closes gap #2 from the marketplace audit. Prior to
this, the marketplace activate path mutated
``marketplace_theme_installations.is_active`` only, leaving
``store_themes`` pointing at the old theme. The customizer reads
``store_themes`` so it kept showing the previous theme.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.core.entities.theme import StoreTheme
from src.core.interfaces.repositories.theme_repository import IStoreThemeRepository
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)
from src.infrastructure.repositories.store_theme_snapshot_repository import (
    StoreThemeSnapshotRepository,
)

logger = logging.getLogger(__name__)


class ThemeActivationService:
    """Atomic activate: snapshot → deactivate_all → upsert_active → mirror."""

    def __init__(
        self,
        store_theme_repo: IStoreThemeRepository,
        snapshot_repo: StoreThemeSnapshotRepository,
        marketplace_repo: MarketplaceRepository,
    ) -> None:
        self._store_theme_repo = store_theme_repo
        self._snapshot_repo = snapshot_repo
        self._marketplace_repo = marketplace_repo

    async def activate(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        theme_id: UUID,
        theme_version_id: UUID,
        reason: str,
        marketplace_theme_id: UUID | None = None,
        seed_customization_v3: dict[str, Any] | None = None,
    ) -> StoreTheme:
        """Swap the store's active theme to ``theme_id`` with full sync.

        Args:
            store_id: The store whose active theme is being swapped.
            tenant_id: The tenant the store belongs to. Required for the
                snapshot row's tenant scoping and for any newly-created
                ``store_themes`` row.
            theme_id: The runtime ``themes.id`` to point ``store_themes``
                at. For marketplace themes, the caller must have already
                upserted a matching ``themes`` row (marketplace_themes
                lives in a separate table and ``store_themes.theme_id``
                points at the runtime entity).
            theme_version_id: The runtime ``theme_versions.id`` matching
                the bundle the storefront should fetch. Again, the
                caller upserts this before calling.
            reason: String stamped on the snapshot row. Identifies the
                entrypoint so future debugging can trace WHY a snapshot
                exists. Conventional values:
                  - ``"marketplace-activate"``  (MarketplaceService)
                  - ``"pre-dev-mode-switch"`` / ``"pre-dev-mode-reconnect"``
                    (dev-mode endpoint)
                  - ``"pre-activation"``  (V2 ThemeService)
            marketplace_theme_id: When the activation came from the
                marketplace catalog, the ``marketplace_themes.id``
                being activated. Causes this service to flip
                ``marketplace_theme_installations.is_active`` to match.
                Omit (None) for dev-mode and V2 paths — this service
                then explicitly clears all marketplace install rows so
                the two tables stay consistent.
            seed_customization_v3: When provided, replaces
                ``customization_v3`` on the new active row. When None,
                preserves whatever's already on the row (V2 path
                semantics — merchant's prior customization survives).

        Returns:
            The activated ``StoreTheme`` entity (with eager-loaded
            theme + version fields). Callers that need to denormalize
            to ``stores.theme_settings`` use this return value to
            avoid a re-fetch.
        """
        # 1) Snapshot prior active state. Unconditional when a prior
        #    exists — matches the pre-Phase-3a behavior of both the
        #    dev-mode endpoint (snapshot always) and ThemeService
        #    (snapshot when prior is a different installation). The
        #    caller-provided ``reason`` records which entrypoint
        #    triggered the swap; same-theme reactivations still get a
        #    "pre-dev-mode-reconnect" or "marketplace-activate" trail
        #    that the admin restore UI can surface later. Storage is
        #    cheap and a missed snapshot is much costlier than a
        #    redundant one.
        prior = await self._store_theme_repo.get_active_for_store(store_id)
        if prior is not None:
            await self._snapshot_repo.create(
                store_id=store_id,
                tenant_id=prior.tenant_id,
                theme_id=prior.theme_id,
                theme_version_id=prior.theme_version_id,
                customization=prior.customization or {},
                customization_v3=prior.customization_v3 or {},
                reason=reason,
            )
            logger.info(
                "theme_activation_snapshot_created",
                extra={
                    "store_id": str(store_id),
                    "from_theme_id": str(prior.theme_id),
                    "to_theme_id": str(theme_id),
                    "same_theme": prior.theme_id == theme_id,
                    "reason": reason,
                },
            )

        # 2) Deactivate every store_themes row for this store.
        #    deactivate_all_for_store sets is_active=False unconditionally;
        #    the upsert below will re-enable just the target row.
        await self._store_theme_repo.deactivate_all_for_store(store_id)

        # 3) Upsert the target row as active. The repo handles both
        #    "found existing row, flip back on" and "no row, create
        #    fresh" — see StoreThemeRepository.upsert_active.
        new_active = await self._store_theme_repo.upsert_active(
            store_id=store_id,
            tenant_id=tenant_id,
            theme_id=theme_id,
            theme_version_id=theme_version_id,
            customization_v3=seed_customization_v3,
        )

        # 4) Mirror to marketplace_theme_installations.
        #    - If this activation came FROM the marketplace, flip the
        #      named installation active (and deactivate any other
        #      marketplace install for this store, which
        #      set_active_installation does as one operation).
        #    - If NOT marketplace-sourced (dev-mode or V2), clear ALL
        #      marketplace installs for this store so the two tables
        #      can't disagree about what's active.
        await self._marketplace_repo.set_active_installation(
            store_id=store_id,
            marketplace_theme_id=marketplace_theme_id,  # None clears all
        )

        logger.info(
            "theme_activation_completed",
            extra={
                "store_id": str(store_id),
                "theme_id": str(theme_id),
                "theme_version_id": str(theme_version_id),
                "marketplace_theme_id": (
                    str(marketplace_theme_id) if marketplace_theme_id else None
                ),
                "reason": reason,
                "seeded_customization": seed_customization_v3 is not None,
            },
        )
        return new_active
