"""Repository for ``store_theme_snapshots``.

A snapshot captures a merchant's existing customization (legacy V2
mirror + V3 payload) immediately before any destructive write. The
two callers today are:

  1. ``POST /stores/{id}/themes/external/dev-mode`` — when a developer
     reconnects with a different ``theme_id`` than what's currently
     installed (covers the case where Empire is active and bon-younes
     gets pointed at via dev-mode).

  2. ``POST /stores/{id}/themes/v2/{installation_id}/activate`` — when
     a merchant clicks Activate on a marketplace theme.

Snapshots are append-only. ``restored_at`` flips when the merchant
clicks Revert; the underlying row stays so we have an audit trail.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.theme import (
    StoreThemeSnapshotModel,
)


class StoreThemeSnapshotRepository:
    """CRUD for store_theme_snapshots. No tenant filter inside — callers
    are expected to be inside an already-tenant-scoped request context."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        theme_id: UUID | None,
        theme_version_id: UUID | None,
        customization: dict,
        customization_v3: dict,
        reason: str,
    ) -> StoreThemeSnapshotModel:
        """Write a new snapshot. The caller decides ``reason`` — common
        values are ``"pre-activation"`` (marketplace activate) and
        ``"pre-dev-mode-switch"`` (CLI reconnect with new theme_id)."""
        snap = StoreThemeSnapshotModel(
            id=uuid4(),
            store_id=store_id,
            tenant_id=tenant_id,
            theme_id=theme_id,
            theme_version_id=theme_version_id,
            # deepcopy so the caller mutating their dict post-call
            # doesn't retroactively edit the snapshot row in memory.
            customization=copy.deepcopy(customization or {}),
            customization_v3=copy.deepcopy(customization_v3 or {}),
            reason=reason,
            created_at=datetime.now(UTC),
        )
        self._session.add(snap)
        await self._session.flush()
        return snap

    async def latest_for_store(self, store_id: UUID) -> StoreThemeSnapshotModel | None:
        """Most recent un-restored snapshot — the one the merchant's
        Revert button reads from."""
        result = await self._session.execute(
            select(StoreThemeSnapshotModel)
            .where(
                StoreThemeSnapshotModel.store_id == store_id,
                StoreThemeSnapshotModel.restored_at.is_(None),
            )
            .order_by(desc(StoreThemeSnapshotModel.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_store(
        self, store_id: UUID, limit: int = 20
    ) -> list[StoreThemeSnapshotModel]:
        """Audit trail. Includes restored snapshots so the merchant
        can see history of theme switches."""
        result = await self._session.execute(
            select(StoreThemeSnapshotModel)
            .where(StoreThemeSnapshotModel.store_id == store_id)
            .order_by(desc(StoreThemeSnapshotModel.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_restored(self, snapshot_id: UUID) -> None:
        """Called when the merchant clicks Revert. Stamps ``restored_at``
        so future "latest snapshot" lookups skip this one (the customization
        they're now on IS this snapshot's contents, so it's no longer a
        meaningful "previous state")."""
        await self._session.execute(
            update(StoreThemeSnapshotModel)
            .where(StoreThemeSnapshotModel.id == snapshot_id)
            .values(restored_at=datetime.now(UTC))
        )
