"""Audit service implementation."""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.interfaces.services.audit_service import (
    AuditEvent,
    AuditEventSeverity,
    AuditEventType,
    AuditLogEntry,
    IAuditService,
)
from src.infrastructure.database.models.audit import AuditLogModel

logger = logging.getLogger(__name__)


class AuditService(IAuditService):
    """Database-backed audit service implementation."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize audit service.

        Args:
            session: Database session for persisting audit logs
        """
        self._session = session

    async def log(self, event: AuditEvent) -> None:
        """Log an audit event to the database.

        Args:
            event: The audit event to log
        """
        try:
            log_entry = AuditLogModel(
                event_type=event.event_type.value,
                severity=event.severity.value,
                user_id=event.user_id,
                customer_id=event.customer_id,
                store_id=event.store_id,
                tenant_id=event.tenant_id,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                action=event.action,
                ip_address=event.ip_address,
                user_agent=event.user_agent,
                details=event.details,
            )
            self._session.add(log_entry)
            await self._session.commit()

            # Also log to application logger for immediate visibility
            logger.info(
                f"AUDIT: {event.event_type.value} | "
                f"user={event.user_id} | "
                f"resource={event.resource_type}:{event.resource_id} | "
                f"ip={event.ip_address}"
            )
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
            # Don't raise - audit logging should not break the main flow
            await self._session.rollback()

    async def log_login_success(
        self,
        user_id: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log successful login."""
        await self.log(
            AuditEvent(
                event_type=AuditEventType.LOGIN_SUCCESS,
                severity=AuditEventSeverity.INFO,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                action="login",
            )
        )

    async def log_login_failed(
        self,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Log failed login attempt."""
        await self.log(
            AuditEvent(
                event_type=AuditEventType.LOGIN_FAILED,
                severity=AuditEventSeverity.WARNING,
                ip_address=ip_address,
                user_agent=user_agent,
                action="login_failed",
                details={
                    "email": email,
                    "reason": reason or "invalid_credentials",
                },
            )
        )

    async def log_permission_denied(
        self,
        user_id: UUID | None,
        resource_type: str,
        action: str,
        ip_address: str | None = None,
    ) -> None:
        """Log permission denied event."""
        await self.log(
            AuditEvent(
                event_type=AuditEventType.PERMISSION_DENIED,
                severity=AuditEventSeverity.WARNING,
                user_id=user_id,
                resource_type=resource_type,
                action=action,
                ip_address=ip_address,
                details={"attempted_action": action},
            )
        )

    async def log_resource_change(
        self,
        event_type: AuditEventType,
        user_id: UUID | None,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
        store_id: UUID | None = None,
    ) -> None:
        """Log resource create/update/delete event."""
        await self.log(
            AuditEvent(
                event_type=event_type,
                severity=AuditEventSeverity.INFO,
                user_id=user_id,
                store_id=store_id,
                resource_type=resource_type,
                resource_id=resource_id,
                action=event_type.value.split("_")[-1],  # "create", "update", "delete"
                details=details or {},
            )
        )

    async def get_logs(
        self,
        user_id: UUID | None = None,
        store_id: UUID | None = None,
        event_type: AuditEventType | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """Query audit logs with filters."""
        query = select(AuditLogModel).order_by(AuditLogModel.created_at.desc())

        if user_id:
            query = query.where(AuditLogModel.user_id == user_id)
        if store_id:
            query = query.where(AuditLogModel.store_id == store_id)
        if event_type:
            query = query.where(AuditLogModel.event_type == event_type.value)
        if start_date:
            query = query.where(AuditLogModel.created_at >= start_date)
        if end_date:
            query = query.where(AuditLogModel.created_at <= end_date)

        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        rows = result.scalars().all()

        return [self._to_entry(row) for row in rows]

    async def get_user_activity(
        self,
        user_id: UUID,
        limit: int = 50,
    ) -> list[AuditLogEntry]:
        """Get recent activity for a user."""
        return await self.get_logs(user_id=user_id, limit=limit)

    def _to_entry(self, model: AuditLogModel) -> AuditLogEntry:
        """Convert database model to domain entity."""
        return AuditLogEntry(
            id=model.id,
            event_type=AuditEventType(model.event_type),
            severity=AuditEventSeverity(model.severity),
            user_id=model.user_id,
            customer_id=model.customer_id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            resource_type=model.resource_type,
            resource_id=model.resource_id,
            action=model.action,
            ip_address=model.ip_address,
            user_agent=model.user_agent,
            details=model.details,
            created_at=model.created_at,
        )


class InMemoryAuditService(IAuditService):
    """In-memory audit service for testing and development."""

    def __init__(self) -> None:
        """Initialize in-memory audit service."""
        self._logs: list[AuditEvent] = []

    async def log(self, event: AuditEvent) -> None:
        """Log an audit event to memory."""
        self._logs.append(event)
        logger.info(
            f"AUDIT (in-memory): {event.event_type.value} | "
            f"user={event.user_id} | "
            f"resource={event.resource_type}:{event.resource_id}"
        )

    async def log_login_success(
        self,
        user_id: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log successful login."""
        await self.log(
            AuditEvent(
                event_type=AuditEventType.LOGIN_SUCCESS,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

    async def log_login_failed(
        self,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Log failed login attempt."""
        await self.log(
            AuditEvent(
                event_type=AuditEventType.LOGIN_FAILED,
                severity=AuditEventSeverity.WARNING,
                ip_address=ip_address,
                user_agent=user_agent,
                details={"email": email, "reason": reason},
            )
        )

    async def log_permission_denied(
        self,
        user_id: UUID | None,
        resource_type: str,
        action: str,
        ip_address: str | None = None,
    ) -> None:
        """Log permission denied event."""
        await self.log(
            AuditEvent(
                event_type=AuditEventType.PERMISSION_DENIED,
                severity=AuditEventSeverity.WARNING,
                user_id=user_id,
                resource_type=resource_type,
                action=action,
                ip_address=ip_address,
            )
        )

    async def log_resource_change(
        self,
        event_type: AuditEventType,
        user_id: UUID | None,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
        store_id: UUID | None = None,
    ) -> None:
        """Log resource change event."""
        await self.log(
            AuditEvent(
                event_type=event_type,
                user_id=user_id,
                store_id=store_id,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details or {},
            )
        )

    async def get_logs(
        self,
        user_id: UUID | None = None,
        store_id: UUID | None = None,
        event_type: AuditEventType | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """Query audit logs with filters."""
        from uuid import uuid4

        filtered = self._logs

        if user_id:
            filtered = [e for e in filtered if e.user_id == user_id]
        if store_id:
            filtered = [e for e in filtered if e.store_id == store_id]
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if start_date:
            filtered = [e for e in filtered if e.timestamp >= start_date]
        if end_date:
            filtered = [e for e in filtered if e.timestamp <= end_date]

        # Sort by timestamp descending
        filtered = sorted(filtered, key=lambda e: e.timestamp, reverse=True)

        # Apply pagination
        paginated = filtered[offset : offset + limit]

        return [
            AuditLogEntry(
                id=uuid4(),
                event_type=e.event_type,
                severity=e.severity,
                user_id=e.user_id,
                customer_id=e.customer_id,
                store_id=e.store_id,
                tenant_id=e.tenant_id,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                action=e.action,
                ip_address=e.ip_address,
                user_agent=e.user_agent,
                details=e.details,
                created_at=e.timestamp,
            )
            for e in paginated
        ]

    async def get_user_activity(
        self,
        user_id: UUID,
        limit: int = 50,
    ) -> list[AuditLogEntry]:
        """Get recent activity for a user."""
        return await self.get_logs(user_id=user_id, limit=limit)

    def clear(self) -> None:
        """Clear all logs (for testing)."""
        self._logs.clear()
