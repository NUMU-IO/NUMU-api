"""Audit service interface for logging security and business events."""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AuditEventType(StrEnum):
    """Types of audit events."""

    # Authentication events
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    TOKEN_REFRESH = "token_refresh"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET_REQUEST = "password_reset_request"
    PASSWORD_RESET_COMPLETE = "password_reset_complete"

    # Authorization events
    PERMISSION_DENIED = "permission_denied"
    ROLE_CHANGED = "role_changed"

    # Resource events
    RESOURCE_CREATE = "resource_create"
    RESOURCE_UPDATE = "resource_update"
    RESOURCE_DELETE = "resource_delete"
    RESOURCE_VIEW = "resource_view"

    # Store events
    STORE_CREATED = "store_created"
    STORE_UPDATED = "store_updated"
    STORE_SUSPENDED = "store_suspended"
    STORE_ACTIVATED = "store_activated"

    # Order events
    ORDER_PLACED = "order_placed"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REFUNDED = "order_refunded"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_FAILED = "payment_failed"

    # Customer events
    CUSTOMER_REGISTERED = "customer_registered"
    CUSTOMER_VERIFIED = "customer_verified"

    # Admin events
    ADMIN_ACTION = "admin_action"
    SETTINGS_CHANGED = "settings_changed"

    # Security events
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"


class AuditEventSeverity(StrEnum):
    """Severity levels for audit events."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditEvent(BaseModel):
    """Audit event data model."""

    event_type: AuditEventType
    severity: AuditEventSeverity = AuditEventSeverity.INFO
    user_id: UUID | None = None
    customer_id: UUID | None = None
    store_id: UUID | None = None
    tenant_id: UUID | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    action: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AuditLogEntry(BaseModel):
    """Persisted audit log entry."""

    id: UUID
    event_type: AuditEventType
    severity: AuditEventSeverity
    user_id: UUID | None
    customer_id: UUID | None
    store_id: UUID | None
    tenant_id: UUID | None
    resource_type: str | None
    resource_id: str | None
    action: str | None
    ip_address: str | None
    user_agent: str | None
    details: dict[str, Any]
    created_at: datetime


class IAuditService(ABC):
    """Interface for audit logging service."""

    @abstractmethod
    async def log(self, event: AuditEvent) -> None:
        """Log an audit event.

        Args:
            event: The audit event to log
        """
        pass

    @abstractmethod
    async def log_login_success(
        self,
        user_id: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log successful login."""
        pass

    @abstractmethod
    async def log_login_failed(
        self,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Log failed login attempt."""
        pass

    @abstractmethod
    async def log_permission_denied(
        self,
        user_id: UUID | None,
        resource_type: str,
        action: str,
        ip_address: str | None = None,
    ) -> None:
        """Log permission denied event."""
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
        """Query audit logs with filters.

        Args:
            user_id: Filter by user ID
            store_id: Filter by store ID
            event_type: Filter by event type
            start_date: Filter events after this date
            end_date: Filter events before this date
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of audit log entries
        """
        pass

    @abstractmethod
    async def get_user_activity(
        self,
        user_id: UUID,
        limit: int = 50,
    ) -> list[AuditLogEntry]:
        """Get recent activity for a user.

        Args:
            user_id: The user ID
            limit: Maximum number of results

        Returns:
            List of audit log entries
        """
        pass
