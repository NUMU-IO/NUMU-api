"""Theme update notification repository implementation (Phase 5.1)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.theme_update_notification import ThemeUpdateNotification
from src.core.interfaces.repositories.theme_update_notification_repository import (
    IThemeUpdateNotificationRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.theme_update_notification import (
    ThemeUpdateNotificationModel,
)


class ThemeUpdateNotificationRepository(IThemeUpdateNotificationRepository):
    """SQLAlchemy implementation of the theme-update notification repo."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(ThemeUpdateNotificationModel.tenant_id == tid)
        return query

    def _to_entity(self, m: ThemeUpdateNotificationModel) -> ThemeUpdateNotification:
        return ThemeUpdateNotification(
            id=m.id,
            store_id=m.store_id,
            tenant_id=m.tenant_id,
            theme_id=m.theme_id,
            from_version_id=m.from_version_id,
            to_version_id=m.to_version_id,
            from_version=m.from_version or "",
            to_version=m.to_version or "",
            classification=m.classification or "automatic",
            changes=m.changes or [],
            release_notes=m.release_notes or "",
            status=m.status or "pending",
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    def _to_model(self, e: ThemeUpdateNotification) -> ThemeUpdateNotificationModel:
        return ThemeUpdateNotificationModel(
            id=e.id,
            store_id=e.store_id,
            tenant_id=e.tenant_id,
            theme_id=e.theme_id,
            from_version_id=e.from_version_id,
            to_version_id=e.to_version_id,
            from_version=e.from_version or "",
            to_version=e.to_version or "",
            classification=e.classification or "automatic",
            changes=e.changes or [],
            release_notes=e.release_notes or "",
            status=e.status or "pending",
            created_at=e.created_at,
            updated_at=e.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> ThemeUpdateNotification | None:
        query = select(ThemeUpdateNotificationModel).where(
            ThemeUpdateNotificationModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        m = result.scalar_one_or_none()
        return self._to_entity(m) if m else None

    async def get_by_store(
        self, store_id: UUID, status: str | None = None
    ) -> list[ThemeUpdateNotification]:
        query = select(ThemeUpdateNotificationModel).where(
            ThemeUpdateNotificationModel.store_id == store_id
        )
        if status:
            query = query.where(ThemeUpdateNotificationModel.status == status)
        query = query.order_by(ThemeUpdateNotificationModel.created_at.desc())
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_for_version(
        self, store_id: UUID, to_version_id: UUID
    ) -> ThemeUpdateNotification | None:
        query = select(ThemeUpdateNotificationModel).where(
            ThemeUpdateNotificationModel.store_id == store_id,
            ThemeUpdateNotificationModel.to_version_id == to_version_id,
        )
        result = await self.session.execute(self._tenant_filter(query))
        m = result.scalar_one_or_none()
        return self._to_entity(m) if m else None

    async def create(self, entity: ThemeUpdateNotification) -> ThemeUpdateNotification:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: ThemeUpdateNotification) -> ThemeUpdateNotification:
        query = select(ThemeUpdateNotificationModel).where(
            ThemeUpdateNotificationModel.id == entity.id
        )
        result = await self.session.execute(self._tenant_filter(query))
        m = result.scalar_one_or_none()
        if not m:
            raise ValueError(f"Notification {entity.id} not found")
        m.status = entity.status
        m.classification = entity.classification
        m.changes = entity.changes or []
        m.release_notes = entity.release_notes or ""
        await self.session.flush()
        await self.session.refresh(m)
        return self._to_entity(m)

    async def delete(self, entity_id: UUID) -> bool:
        query = select(ThemeUpdateNotificationModel).where(
            ThemeUpdateNotificationModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        m = result.scalar_one_or_none()
        if not m:
            return False
        await self.session.delete(m)
        await self.session.flush()
        return True
