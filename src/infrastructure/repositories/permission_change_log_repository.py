"""Permission change log repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.permission_change_log import (
    PermissionChangeAction,
    PermissionChangeLogModel,
    PermissionChangeTargetType,
)


class PermissionChangeLogRepository:
    """Repository for permission change logs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        tenant_id: UUID | None,
        actor_user_id: UUID | None,
        target_type: PermissionChangeTargetType,
        target_id: UUID,
        action: PermissionChangeAction,
        before: dict | None = None,
        after: dict | None = None,
        diff: dict | None = None,
        reason: str | None = None,
        ip: str | None = None,
    ) -> PermissionChangeLogModel:
        """Create a new permission change log entry."""
        log = PermissionChangeLogModel(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            before=before,
            after=after,
            diff=diff,
            reason=reason,
            ip=ip,
            created_at=datetime.utcnow(),
        )
        self.session.add(log)
        await self.session.flush()
        await self.session.refresh(log)
        return log

    async def get_by_tenant(
        self,
        tenant_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[PermissionChangeLogModel]:
        """Get logs for a tenant."""
        result = await self.session.execute(
            select(PermissionChangeLogModel)
            .where(PermissionChangeLogModel.tenant_id == tenant_id)
            .order_by(PermissionChangeLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_target(
        self, target_type: PermissionChangeTargetType, target_id: UUID
    ) -> list[PermissionChangeLogModel]:
        """Get logs for a specific target."""
        result = await self.session.execute(
            select(PermissionChangeLogModel)
            .where(
                PermissionChangeLogModel.target_type == target_type,
                PermissionChangeLogModel.target_id == target_id,
            )
            .order_by(PermissionChangeLogModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_actor(
        self, actor_user_id: UUID, limit: int = 100
    ) -> list[PermissionChangeLogModel]:
        """Get logs by actor."""
        result = await self.session.execute(
            select(PermissionChangeLogModel)
            .where(PermissionChangeLogModel.actor_user_id == actor_user_id)
            .order_by(PermissionChangeLogModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent(
        self,
        tenant_id: UUID | None = None,
        limit: int = 50,
    ) -> list[PermissionChangeLogModel]:
        """Get recent logs."""
        query = select(PermissionChangeLogModel)
        if tenant_id:
            query = query.where(PermissionChangeLogModel.tenant_id == tenant_id)
        query = query.order_by(PermissionChangeLogModel.created_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())
