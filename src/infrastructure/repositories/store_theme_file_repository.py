"""Repository for per-store theme code-editor files (store_theme_files).

Concrete repository (no interface) used directly by the theme code service and
the build task's materialize step — mirrors StoreThemeSnapshotRepository's
direct-instantiation style. App-level tenant scoping: every query filters by
store_id, which the route layer has already verified the caller owns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.theme import StoreThemeFile
from src.infrastructure.database.models.tenant.theme import StoreThemeFileModel


class StoreThemeFileRepository:
    """Manages the editable source files of a store's theme workspace."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: StoreThemeFileModel) -> StoreThemeFile:
        return StoreThemeFile(
            id=UUID(str(model.id)),
            created_at=model.created_at,
            updated_at=model.updated_at,
            tenant_id=UUID(str(model.tenant_id)),
            store_id=UUID(str(model.store_id)),
            path=model.path,
            content=model.content or "",
        )

    async def list_for_store(self, store_id: UUID) -> list[StoreThemeFile]:
        """All files for a store, ordered by path (stable file-tree order)."""
        result = await self.session.execute(
            select(StoreThemeFileModel)
            .where(StoreThemeFileModel.store_id == str(store_id))
            .order_by(StoreThemeFileModel.path.asc())
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get(self, store_id: UUID, path: str) -> StoreThemeFile | None:
        """Fetch one file by its path within a store."""
        result = await self.session.execute(
            select(StoreThemeFileModel).where(
                StoreThemeFileModel.store_id == str(store_id),
                StoreThemeFileModel.path == path,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def count_for_store(self, store_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count(StoreThemeFileModel.id)).where(
                StoreThemeFileModel.store_id == str(store_id)
            )
        )
        return result.scalar() or 0

    async def upsert(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        path: str,
        content: str,
    ) -> StoreThemeFile:
        """Create or overwrite a single file at ``path``."""
        result = await self.session.execute(
            select(StoreThemeFileModel).where(
                StoreThemeFileModel.store_id == str(store_id),
                StoreThemeFileModel.path == path,
            )
        )
        model = result.scalar_one_or_none()
        if model is not None:
            model.content = content
            model.updated_at = datetime.now(UTC)
        else:
            # Pass real UUID objects (not str): the PK is UUID(as_uuid=True)
            # and SQLAlchemy matches it as an insert sentinel — a str PK
            # breaks insertmanyvalues sentinel matching (see bulk_upsert).
            model = StoreThemeFileModel(
                id=uuid4(),
                tenant_id=tenant_id,
                store_id=store_id,
                path=path,
                content=content,
            )
            self.session.add(model)
        await self.session.flush()
        refreshed = await self.session.execute(
            select(StoreThemeFileModel).where(StoreThemeFileModel.id == model.id)
        )
        return self._to_entity(refreshed.scalar_one())

    async def bulk_upsert(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        files: dict[str, str],
    ) -> int:
        """Create/overwrite many files at once (used by scaffold). Returns count."""
        existing = await self.session.execute(
            select(StoreThemeFileModel).where(
                StoreThemeFileModel.store_id == str(store_id)
            )
        )
        by_path = {m.path: m for m in existing.scalars().all()}
        for path, content in files.items():
            model = by_path.get(path)
            if model is not None:
                model.content = content
            else:
                # UUID objects, not str — bulk insert matches the UUID PK as a
                # sentinel and a str PK raises "Can't match sentinel values".
                self.session.add(
                    StoreThemeFileModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        store_id=store_id,
                        path=path,
                        content=content,
                    )
                )
        await self.session.flush()
        return len(files)

    async def delete(self, store_id: UUID, path: str) -> bool:
        """Delete one file by path. Returns whether a row was removed."""
        result = await self.session.execute(
            select(StoreThemeFileModel).where(
                StoreThemeFileModel.store_id == str(store_id),
                StoreThemeFileModel.path == path,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def delete_all_for_store(self, store_id: UUID) -> int:
        """Wipe the whole workspace for a store. Returns rows deleted."""
        result = await self.session.execute(
            sa_delete(StoreThemeFileModel).where(
                StoreThemeFileModel.store_id == str(store_id)
            )
        )
        await self.session.flush()
        return result.rowcount or 0
